import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models.expressions import RawSQL
from django.db.models.functions import Lower

GUARDS = r"""
CREATE FUNCTION public.sc02_material_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $function$
DECLARE category_workshop bigint; category_key text; category_name text; category_status text;
        unit_workshop bigint; unit_status text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'materials cannot be deleted' USING ERRCODE='23503';
  END IF;
  SELECT workshop_id, machine_key, name, status
    INTO category_workshop, category_key, category_name, category_status
    FROM public.material_category WHERE id=NEW.category_id;
  SELECT workshop_id, status INTO unit_workshop, unit_status
    FROM public.unit_type WHERE id=NEW.unit_id;
  IF NOT ((category_workshop=NEW.workshop_id) OR
          (category_workshop IS NULL AND category_key='undefined' AND category_name='undefined' AND category_status='active'))
     OR unit_workshop<>NEW.workshop_id THEN
    RAISE EXCEPTION 'invalid material dependency scope' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' AND NEW.unit_id<>OLD.unit_id AND
     EXISTS(SELECT 1 FROM public.material_variant WHERE material_id=OLD.id) THEN
    RAISE EXCEPTION 'material unit is locked' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' AND OLD.status='active' AND NEW.status='archived' AND
     EXISTS(SELECT 1 FROM public.material_variant WHERE material_id=OLD.id AND status='active') THEN
    RAISE EXCEPTION 'active variants block material retirement' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $function$;
CREATE TRIGGER cst_317_318_material_guard
BEFORE INSERT OR UPDATE OR DELETE ON public.material
FOR EACH ROW EXECUTE FUNCTION public.sc02_material_guard();

CREATE FUNCTION public.sc02_variant_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $function$
DECLARE parent_workshop bigint;
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'material variants cannot be deleted' USING ERRCODE='23503';
  END IF;
  SELECT workshop_id INTO parent_workshop FROM public.material WHERE id=NEW.material_id;
  IF parent_workshop IS NULL OR parent_workshop<>NEW.workshop_id THEN
    RAISE EXCEPTION 'material variant Workshop mismatch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $function$;
CREATE TRIGGER cst_323_material_variant_scope
BEFORE INSERT OR UPDATE OR DELETE ON public.material_variant
FOR EACH ROW EXECUTE FUNCTION public.sc02_variant_guard();

CREATE FUNCTION public.sc02_stock_effect_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $function$
DECLARE variant_workshop bigint; actor_workshop bigint;
BEGIN
  IF TG_OP IN ('UPDATE','DELETE') THEN
    RAISE EXCEPTION 'stock effects are immutable' USING ERRCODE='23514';
  END IF;
  SELECT workshop_id INTO variant_workshop FROM public.material_variant
   WHERE id=NEW.material_variant_id;
  IF variant_workshop IS NULL OR variant_workshop<>NEW.workshop_id THEN
    RAISE EXCEPTION 'stock effect Workshop mismatch' USING ERRCODE='23514';
  END IF;
  IF NEW.actor_or_system_id IS NOT NULL THEN
    SELECT workshop_id INTO actor_workshop FROM public.user_account
     WHERE id=NEW.actor_or_system_id;
    IF actor_workshop IS NULL OR actor_workshop<>NEW.workshop_id THEN
      RAISE EXCEPTION 'stock effect actor Workshop mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $function$;
CREATE TRIGGER cst_334_349_stock_effect_guard
BEFORE INSERT OR UPDATE OR DELETE ON public.stock_effect
FOR EACH ROW EXECUTE FUNCTION public.sc02_stock_effect_guard();

CREATE FUNCTION public.sc02_stock_projection_sync()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $function$
DECLARE changed integer;
BEGIN
  UPDATE public.material_variant
     SET current_stock=NEW.balance_after, version=version+1
   WHERE id=NEW.material_variant_id
     AND workshop_id=NEW.workshop_id
     AND current_stock=NEW.balance_before;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed<>1 THEN
    RAISE EXCEPTION 'stock projection conflict' USING ERRCODE='40001';
  END IF;
  RETURN NEW;
END $function$;
CREATE TRIGGER cst_351_stock_projection_sync
AFTER INSERT ON public.stock_effect
FOR EACH ROW EXECUTE FUNCTION public.sc02_stock_projection_sync();

CREATE FUNCTION public.sc02_material_receipt_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
BEGIN
  RAISE EXCEPTION 'material command receipts are immutable' USING ERRCODE='23514';
END $function$;
CREATE TRIGGER cst_367_material_receipt_immutable
BEFORE UPDATE OR DELETE ON public.material_command_receipt
FOR EACH ROW EXECUTE FUNCTION public.sc02_material_receipt_guard();

CREATE FUNCTION public.sc02_material_dependency_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $function$
BEGIN
  IF TG_OP='UPDATE' AND OLD.status='active' AND NEW.status='retired' AND
     EXISTS(SELECT 1 FROM public.material WHERE status='active' AND
       ((TG_TABLE_NAME='unit_type' AND unit_id=OLD.id) OR
        (TG_TABLE_NAME='material_category' AND category_id=OLD.id))) THEN
    RAISE EXCEPTION 'active materials block dependency retirement' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $function$;
CREATE TRIGGER cst_sc02_unit_material_dependency
BEFORE UPDATE ON public.unit_type FOR EACH ROW EXECUTE FUNCTION public.sc02_material_dependency_guard();
CREATE TRIGGER cst_sc02_category_material_dependency
BEFORE UPDATE ON public.material_category FOR EACH ROW EXECUTE FUNCTION public.sc02_material_dependency_guard();
"""

REVERSE_GUARDS = r"""
DROP TRIGGER IF EXISTS cst_sc02_category_material_dependency ON public.material_category;
DROP TRIGGER IF EXISTS cst_sc02_unit_material_dependency ON public.unit_type;
DROP FUNCTION IF EXISTS public.sc02_material_dependency_guard();
DROP TRIGGER IF EXISTS cst_367_material_receipt_immutable ON public.material_command_receipt;
DROP FUNCTION IF EXISTS public.sc02_material_receipt_guard();
DROP TRIGGER IF EXISTS cst_351_stock_projection_sync ON public.stock_effect;
DROP FUNCTION IF EXISTS public.sc02_stock_projection_sync();
DROP TRIGGER IF EXISTS cst_334_349_stock_effect_guard ON public.stock_effect;
DROP FUNCTION IF EXISTS public.sc02_stock_effect_guard();
DROP TRIGGER IF EXISTS cst_323_material_variant_scope ON public.material_variant;
DROP FUNCTION IF EXISTS public.sc02_variant_guard();
DROP TRIGGER IF EXISTS cst_317_318_material_guard ON public.material;
DROP FUNCTION IF EXISTS public.sc02_material_guard();
"""


# The state operations intentionally remain compact so the trigger contract stays
# readable beside them; Django's migration loader treats this as generated state.
# fmt: off
class Migration(migrations.Migration):
    dependencies = [
        ("events", "0002_event_subject"),
        ("workshops", "0004_sc01_library_sources"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Material",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.TextField()),
                ("status", models.TextField(choices=[("active", "Active"), ("archived", "Archived")], db_default="active", default="active")),
                ("version", models.PositiveIntegerField(db_default=1, default=1)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="materials", to="workshops.materialcategory")),
                ("unit", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="materials", to="workshops.unittype")),
                ("workshop", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="materials", to="workshops.workshop")),
            ],
            options={
                "db_table": "material",
                "indexes": [models.Index(fields=["workshop", "status"], name="idx_069_material_scope"), models.Index(fields=["unit"], name="idx_070_material_unit"), models.Index(fields=["category"], name="idx_071_material_category")],
                "constraints": [models.CheckConstraint(condition=~models.Q(name=""), name="cst_313_material_name_nonblank"), models.CheckConstraint(condition=models.Q(status__in=("active", "archived")), name="cst_314_material_status"), models.CheckConstraint(condition=models.Q(version__gt=0), name="cst_315_material_version"), models.UniqueConstraint(Lower("name"), "workshop", name="cst_316_material_name_uniq")],
            },
        ),
        migrations.CreateModel(
            name="MaterialVariant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("spec_label", models.TextField()),
                ("current_stock", models.DecimalField(db_default=0, decimal_places=4, default=0, max_digits=14)),
                ("min_threshold", models.DecimalField(decimal_places=4, max_digits=14)),
                ("status", models.TextField(choices=[("active", "Active"), ("archived", "Archived")], db_default="active", default="active")),
                ("version", models.PositiveIntegerField(db_default=1, default=1)),
                ("material", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="variants", to="workshops.material")),
                ("workshop", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="material_variants", to="workshops.workshop")),
            ],
            options={
                "db_table": "material_variant",
                "indexes": [models.Index(fields=["material", "status"], name="idx_072_variant_material"), models.Index(fields=["workshop", "status"], name="idx_073_variant_workshop")],
                "constraints": [models.CheckConstraint(condition=~models.Q(spec_label=""), name="cst_324_material_variant_label"), models.UniqueConstraint(Lower("spec_label"), "material", name="cst_325_material_variant_label_uniq"), models.CheckConstraint(condition=models.Q(current_stock__gte=0), name="cst_326_material_variant_stock"), models.CheckConstraint(condition=models.Q(min_threshold__gte=0), name="cst_327_material_variant_threshold"), models.CheckConstraint(condition=models.Q(status__in=("active", "archived")), name="cst_328_material_variant_status"), models.CheckConstraint(condition=models.Q(version__gt=0), name="cst_329_material_variant_version")],
            },
        ),
        migrations.CreateModel(
            name="StockEffect",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("effect_type", models.TextField()), ("source_type", models.TextField()),
                ("command_identity", models.TextField()), ("correlation_identity", models.TextField()),
                ("source_identity", models.BigIntegerField(blank=True, null=True)), ("source_version", models.IntegerField(blank=True, null=True)),
                ("delta", models.DecimalField(decimal_places=4, max_digits=14)), ("balance_before", models.DecimalField(decimal_places=4, max_digits=14)), ("balance_after", models.DecimalField(decimal_places=4, max_digits=14)),
                ("reason", models.TextField(blank=True, null=True)), ("category", models.TextField(blank=True, null=True)),
                ("stock_projection_version", models.PositiveIntegerField()), ("accepted_at", models.DateTimeField(db_default=RawSQL("now()", ()))),
                ("actor_or_system", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name="stock_effects", to=settings.AUTH_USER_MODEL)),
                ("material_variant", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="stock_effects", to="workshops.materialvariant")),
                ("workshop", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="stock_effects", to="workshops.workshop")),
            ],
            options={
                "db_table": "stock_effect",
                "indexes": [models.Index(fields=["material_variant", "accepted_at"], name="idx_074_effect_variant_time"), models.Index(condition=models.Q(source_identity__isnull=False), fields=["source_type", "source_identity"], name="idx_075_effect_source"), models.Index(fields=["correlation_identity"], name="idx_076_effect_correlation")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(effect_type__in=("opening_balance", "operation_consumption", "purchase_order_arrival", "stock_write_off", "manual_adjustment")), name="cst_335_stock_effect_type"),
                    models.CheckConstraint(condition=models.Q(source_type__in=("material_variant_creation", "operation_material_settlement", "purchase_order_arrival", "stock_write_off", "manual_adjustment")), name="cst_336_stock_effect_source"),
                    models.CheckConstraint(condition=models.Q(effect_type="opening_balance", source_type="material_variant_creation") | models.Q(effect_type="operation_consumption", source_type="operation_material_settlement") | models.Q(effect_type="purchase_order_arrival", source_type="purchase_order_arrival") | models.Q(effect_type="stock_write_off", source_type="stock_write_off") | models.Q(effect_type="manual_adjustment", source_type="manual_adjustment"), name="cst_337_stock_effect_pair"),
                    models.CheckConstraint(condition=models.Q(source_version__isnull=True) | models.Q(source_version__gte=0), name="cst_340_stock_effect_source_version"),
                    models.CheckConstraint(condition=models.Q(actor_or_system__isnull=False) | models.Q(effect_type="purchase_order_arrival"), name="cst_341_stock_effect_actor"),
                    models.CheckConstraint(condition=models.Q(effect_type__in=("operation_consumption", "stock_write_off"), delta__lt=0) | models.Q(effect_type="purchase_order_arrival", delta__gt=0) | models.Q(effect_type="opening_balance", delta__gte=0) | (models.Q(effect_type="manual_adjustment") & ~models.Q(delta=0)), name="cst_342_stock_effect_sign"),
                    models.CheckConstraint(condition=models.Q(balance_before__gte=0), name="cst_343_stock_effect_before"),
                    models.CheckConstraint(condition=models.Q(balance_after=models.F("balance_before") + models.F("delta")) & models.Q(balance_after__gte=0), name="cst_344_stock_effect_balance"),
                    models.CheckConstraint(condition=(models.Q(effect_type__in=("stock_write_off", "manual_adjustment")) & models.Q(reason__isnull=False) & ~models.Q(reason="")) | (~models.Q(effect_type__in=("stock_write_off", "manual_adjustment")) & models.Q(reason__isnull=True)), name="cst_345_stock_effect_reason"),
                    models.CheckConstraint(condition=models.Q(stock_projection_version__gt=0), name="cst_347_stock_effect_projection_version"),
                ],
            },
        ),
        migrations.CreateModel(
            name="MaterialCommandReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("target_type", models.TextField()), ("target_id", models.BigIntegerField()), ("idempotency_key", models.TextField()), ("command_family", models.TextField()), ("request_fingerprint", models.TextField()), ("result_version", models.PositiveIntegerField()), ("result_summary", models.JSONField(blank=True, null=True)), ("created_at", models.DateTimeField(db_default=RawSQL("now()", ()))),
                ("actor_user", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="material_receipts", to=settings.AUTH_USER_MODEL)),
                ("workshop", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="material_receipts", to="workshops.workshop")),
            ],
            options={
                "db_table": "material_command_receipt",
                "indexes": [models.Index(fields=["target_type", "target_id"], name="idx_078_material_receipt")],
                "constraints": [models.CheckConstraint(condition=models.Q(target_type__in=("material", "material_variant")), name="cst_362_material_receipt_target"), models.CheckConstraint(condition=models.Q(command_family__in=("edit", "archive", "restore", "manual_count")), name="cst_364_material_receipt_family"), models.CheckConstraint(condition=models.Q(command_family="manual_count", target_type="material_variant") | (~models.Q(command_family="manual_count") & models.Q(target_type__in=("material", "material_variant"))), name="cst_365_material_receipt_pair"), models.UniqueConstraint(fields=("workshop", "actor_user", "idempotency_key"), name="cst_366_material_receipt_key_uniq")],
            },
        ),
        migrations.RunSQL(GUARDS, REVERSE_GUARDS),
    ]
# fmt: on
