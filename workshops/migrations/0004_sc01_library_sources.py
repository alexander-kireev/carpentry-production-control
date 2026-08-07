import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models
from django.db.models.expressions import RawSQL
from django.db.models.functions import Lower

GUARDS = r"""
CREATE FUNCTION public.sc01_library_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $function$
DECLARE role_workshop bigint; type_workshop bigint; type_clearance boolean; role_key text; type_key text;
BEGIN
  IF TG_TABLE_NAME IN ('unit_type','material_category','shift_definition','configuration_command_receipt')
     AND TG_OP IN ('UPDATE','DELETE') AND TG_TABLE_NAME = 'configuration_command_receipt' THEN
    RAISE EXCEPTION 'configuration receipts are immutable' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME IN ('unit_type','material_category','shift_definition') AND TG_OP='DELETE' THEN
    RAISE EXCEPTION 'library rows cannot be deleted' USING ERRCODE='23503';
  END IF;
  IF TG_TABLE_NAME='material_category' THEN
    IF NEW.workshop_id IS NULL AND NOT (NEW.machine_key='undefined' AND NEW.name='undefined' AND NEW.status='active') THEN
      RAISE EXCEPTION 'invalid global material category' USING ERRCODE='23514';
    ELSIF NEW.workshop_id IS NOT NULL AND NEW.machine_key IS NOT NULL THEN
      RAISE EXCEPTION 'workshop material category cannot have machine key' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND OLD.machine_key IS NOT NULL AND ROW(NEW.machine_key,NEW.name,NEW.workshop_id,NEW.status) IS DISTINCT FROM ROW(OLD.machine_key,OLD.name,OLD.workshop_id,OLD.status) THEN
      RAISE EXCEPTION 'protected material category is immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $function$;
CREATE TRIGGER cst_sc01_unit_guard BEFORE DELETE ON public.unit_type FOR EACH ROW EXECUTE FUNCTION public.sc01_library_guard();
CREATE TRIGGER cst_sc01_category_guard BEFORE INSERT OR UPDATE OR DELETE ON public.material_category FOR EACH ROW EXECUTE FUNCTION public.sc01_library_guard();
CREATE TRIGGER cst_sc01_shift_guard BEFORE DELETE ON public.shift_definition FOR EACH ROW EXECUTE FUNCTION public.sc01_library_guard();
CREATE TRIGGER cst_sc01_receipt_guard BEFORE UPDATE OR DELETE ON public.configuration_command_receipt FOR EACH ROW EXECUTE FUNCTION public.sc01_library_guard();

CREATE FUNCTION public.sc01_days_are_canonical(smallint[])
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog
RETURN cardinality($1)>0 AND $1 <@ ARRAY[0,1,2,3,4,5,6]::smallint[] AND
       $1 = ARRAY(SELECT DISTINCT v FROM unnest($1) v ORDER BY v);
ALTER TABLE public.shift_definition ADD CONSTRAINT cst_070_shift_definition_days
CHECK (public.sc01_days_are_canonical(days)) NOT VALID;
ALTER TABLE public.shift_definition VALIDATE CONSTRAINT cst_070_shift_definition_days;

ALTER TABLE public.configuration_command_receipt ADD CONSTRAINT cst_689_configuration_command_type
CHECK (command_type IN ('workshop_role_create','operation_type_create','unit_type_create','material_category_create','shift_definition_create','material_create','material_variant_create','station_create','add_selected_configuration'));
ALTER TABLE public.configuration_command_receipt ADD CONSTRAINT cst_690_configuration_result_type
CHECK (result_type IN ('workshop_role','operation_type','unit_type','material_category','shift_definition','material','material_variant','station','configuration_batch'));
ALTER TABLE public.configuration_command_receipt ADD CONSTRAINT cst_691_configuration_result_shape
CHECK (jsonb_typeof(result_summary)='object' AND result_summary <> '{}'::jsonb AND (result_type='configuration_batch' OR result_id IS NOT NULL));

CREATE FUNCTION public.sc01_default_clearance_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $function$
DECLARE role_workshop bigint; type_workshop bigint; type_clearance boolean; role_key text; type_key text; type_status text;
BEGIN
 SELECT workshop_id,machine_key INTO role_workshop,role_key FROM public.workshop_role WHERE id=NEW.workshop_role_id;
 SELECT workshop_id,requires_clearance,machine_key,status INTO type_workshop,type_clearance,type_key,type_status FROM public.operation_type WHERE id=NEW.operation_type_id;
 IF role_workshop IS NULL OR role_key IS NOT NULL OR type_status<>'active' OR
    NOT (type_clearance OR (type_workshop IS NULL AND type_key='other')) OR
    NOT (type_workshop=role_workshop OR (type_workshop IS NULL AND type_key='other')) THEN
   RAISE EXCEPTION 'invalid default clearance relation' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $function$;
CREATE TRIGGER cst_083_default_clearance_guard BEFORE INSERT OR UPDATE ON public.workshop_role_default_clearance FOR EACH ROW EXECUTE FUNCTION public.sc01_default_clearance_guard();
"""

REVERSE_GUARDS = r"""
DROP TRIGGER IF EXISTS cst_083_default_clearance_guard ON public.workshop_role_default_clearance;
DROP FUNCTION IF EXISTS public.sc01_default_clearance_guard();
DROP TRIGGER IF EXISTS cst_sc01_receipt_guard ON public.configuration_command_receipt;
DROP TRIGGER IF EXISTS cst_sc01_shift_guard ON public.shift_definition;
DROP TRIGGER IF EXISTS cst_sc01_category_guard ON public.material_category;
DROP TRIGGER IF EXISTS cst_sc01_unit_guard ON public.unit_type;
DROP FUNCTION IF EXISTS public.sc01_library_guard();
ALTER TABLE public.shift_definition DROP CONSTRAINT IF EXISTS cst_070_shift_definition_days;
DROP FUNCTION IF EXISTS public.sc01_days_are_canonical(smallint[]);
"""


def seed_material_category(apps, schema_editor):
    model = apps.get_model("workshops", "MaterialCategory")
    row, _ = model.objects.using(schema_editor.connection.alias).get_or_create(
        machine_key="undefined",
        defaults={
            "workshop_id": None,
            "name": "undefined",
            "status": "active",
            "version": 1,
        },
    )
    if not (
        row.workshop_id is None
        and row.name == "undefined"
        and row.status == "active"
        and row.version > 0
    ):
        raise RuntimeError("Protected material category bootstrap conflict")
    if (
        model.objects.using(schema_editor.connection.alias)
        .filter(workshop_id__isnull=True)
        .count()
        != 1
    ):
        raise RuntimeError("Protected material category bootstrap is not exact")


# fmt: off
class Migration(migrations.Migration):
    dependencies = [
        ("events", "0002_event_subject"),
        ("identity", "0005_manager_invitation_delivery"),
        ("workshops", "0003_seed_protected_identities"),
    ]

    operations = [
        migrations.CreateModel(name="UnitType", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.TextField()), ("abbreviation", models.TextField()), ("status", models.TextField(choices=[("active","Active"),("retired","Retired")], db_default="active", default="active")), ("version", models.PositiveIntegerField(db_default=1, default=1)), ("workshop", models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name="unit_types", to="workshops.workshop"))], options={"db_table":"unit_type"}),
        migrations.CreateModel(name="MaterialCategory", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.TextField()), ("machine_key", models.TextField(blank=True,null=True)), ("status", models.TextField(choices=[("active","Active"),("retired","Retired")],db_default="active",default="active")), ("version",models.PositiveIntegerField(db_default=1,default=1)), ("workshop",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.RESTRICT,related_name="material_categories",to="workshops.workshop"))], options={"db_table":"material_category"}),
        migrations.CreateModel(name="ShiftDefinition", fields=[("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),("name",models.TextField()),("start_time",models.TimeField()),("end_time",models.TimeField()),("days",django.contrib.postgres.fields.ArrayField(base_field=models.SmallIntegerField(),size=None)),("status",models.TextField(choices=[("active","Active"),("retired","Retired")],db_default="active",default="active")),("version",models.PositiveIntegerField(db_default=1,default=1)),("workshop",models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT,related_name="shift_definitions",to="workshops.workshop"))],options={"db_table":"shift_definition"}),
        migrations.CreateModel(name="WorkshopRoleDefaultClearance",fields=[("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),("operation_type",models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT,related_name="default_role_links",to="workshops.operationtype")),("workshop_role",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="default_clearance_links",to="workshops.workshoprole"))],options={"db_table":"workshop_role_default_clearance"}),
        migrations.CreateModel(name="ConfigurationCommandReceipt",fields=[("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),("command_type",models.TextField()),("submission_key",models.TextField()),("fingerprint_version",models.SmallIntegerField(db_default=1,default=1)),("payload_fingerprint",models.TextField()),("result_type",models.TextField()),("result_id",models.BigIntegerField(blank=True,null=True)),("result_summary",models.JSONField(db_default={},default=dict)),("state",models.TextField(db_default="committed",default="committed")),("committed_at",models.DateTimeField(db_default=RawSQL("now()",()))),("actor_user",models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT,related_name="configuration_receipts",to="identity.user")),("workshop",models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT,related_name="configuration_receipts",to="workshops.workshop"))],options={"db_table":"configuration_command_receipt"}),
        migrations.AddConstraint(model_name="unittype",constraint=models.CheckConstraint(condition=models.Q(("status__in",("active","retired"))),name="cst_050_unit_type_status")), migrations.AddConstraint(model_name="unittype",constraint=models.CheckConstraint(condition=models.Q(("version__gt",0)),name="cst_051_unit_type_version")), migrations.AddConstraint(model_name="unittype",constraint=models.CheckConstraint(condition=models.Q(models.Q(("name",""),_negated=True),models.Q(("abbreviation",""),_negated=True)),name="cst_052_unit_type_labels_nonempty")), migrations.AddConstraint(model_name="unittype",constraint=models.UniqueConstraint(Lower("name"),models.F("workshop"),name="cst_053_unit_type_name_uniq")), migrations.AddConstraint(model_name="unittype",constraint=models.UniqueConstraint(Lower("abbreviation"),models.F("workshop"),name="cst_054_unit_type_abbreviation_uniq")), migrations.AddIndex(model_name="unittype",index=models.Index(fields=["workshop","status"],name="idx_015_unit_scope")),
        migrations.AddConstraint(model_name="materialcategory",constraint=models.CheckConstraint(condition=models.Q(("status__in",("active","retired"))),name="cst_055_material_category_status")), migrations.AddConstraint(model_name="materialcategory",constraint=models.CheckConstraint(condition=models.Q(("version__gt",0)),name="cst_056_material_category_version")), migrations.AddConstraint(model_name="materialcategory",constraint=models.CheckConstraint(condition=models.Q(("name",""),_negated=True),name="cst_057_material_category_name")), migrations.AddConstraint(model_name="materialcategory",constraint=models.CheckConstraint(condition=models.Q(("machine_key__isnull",True),("status","active"),_connector="OR"),name="cst_058_material_category_sentinel_active")), migrations.AddConstraint(model_name="materialcategory",constraint=models.CheckConstraint(condition=models.Q(("workshop__isnull",True),models.Q(("name__iexact","undefined"),_negated=True),_connector="OR"),name="cst_059_material_category_reserved_name")), migrations.AddConstraint(model_name="materialcategory",constraint=models.UniqueConstraint(Lower("name"),models.F("workshop"),condition=models.Q(("workshop__isnull",False)),name="cst_060_material_category_name_uniq")), migrations.AddConstraint(model_name="materialcategory",constraint=models.UniqueConstraint(Lower("name"),condition=models.Q(("workshop__isnull",True)),name="cst_061_global_material_category_name_uniq")), migrations.AddConstraint(model_name="materialcategory",constraint=models.UniqueConstraint(condition=models.Q(("workshop__isnull",True)),fields=("machine_key",),name="cst_062_global_material_category_key_uniq")), migrations.AddIndex(model_name="materialcategory",index=models.Index(fields=["workshop","status"],name="idx_016_category_scope")),
        migrations.AddConstraint(model_name="shiftdefinition",constraint=models.CheckConstraint(condition=models.Q(("status__in",("active","retired"))),name="cst_063_shift_definition_status")), migrations.AddConstraint(model_name="shiftdefinition",constraint=models.CheckConstraint(condition=models.Q(("version__gt",0)),name="cst_064_shift_definition_version")), migrations.AddConstraint(model_name="shiftdefinition",constraint=models.CheckConstraint(condition=models.Q(("name",""),_negated=True),name="cst_065_shift_definition_name")), migrations.AddConstraint(model_name="shiftdefinition",constraint=models.CheckConstraint(condition=models.Q(("start_time__lt",models.F("end_time"))),name="cst_066_shift_definition_same_day")), migrations.AddConstraint(model_name="shiftdefinition",constraint=models.UniqueConstraint(Lower("name"),models.F("workshop"),condition=models.Q(("status","active")),name="cst_067_shift_definition_active_name_uniq")), migrations.AddConstraint(model_name="shiftdefinition",constraint=models.UniqueConstraint(condition=models.Q(("status","active")),fields=("workshop","start_time","end_time","days"),name="cst_068_shift_definition_active_shape_uniq")),
        migrations.AddConstraint(model_name="workshoproledefaultclearance",constraint=models.UniqueConstraint(fields=("workshop_role","operation_type"),name="cst_069_workshop_role_clearance_uniq")),
        migrations.AddConstraint(model_name="configurationcommandreceipt",constraint=models.CheckConstraint(condition=models.Q(("fingerprint_version__gt",0)),name="cst_683_configuration_fingerprint_version")), migrations.AddConstraint(model_name="configurationcommandreceipt",constraint=models.UniqueConstraint(fields=("workshop","command_type","submission_key"),name="cst_684_configuration_receipt_key_uniq")), migrations.AddConstraint(model_name="configurationcommandreceipt",constraint=models.CheckConstraint(condition=models.Q(("state","committed")),name="cst_685_configuration_receipt_state")), migrations.AddConstraint(model_name="configurationcommandreceipt",constraint=models.CheckConstraint(condition=models.Q(("result_id__gt",0)),name="cst_688_configuration_result_id_positive")), migrations.AddConstraint(model_name="configurationcommandreceipt",constraint=models.CheckConstraint(condition=(models.Q(command_type="workshop_role_create",result_type="workshop_role",result_id__isnull=False)|models.Q(command_type="operation_type_create",result_type="operation_type",result_id__isnull=False)|models.Q(command_type="unit_type_create",result_type="unit_type",result_id__isnull=False)|models.Q(command_type="material_category_create",result_type="material_category",result_id__isnull=False)|models.Q(command_type="shift_definition_create",result_type="shift_definition",result_id__isnull=False)|models.Q(command_type="material_create",result_type="material",result_id__isnull=False)|models.Q(command_type="material_variant_create",result_type="material_variant",result_id__isnull=False)|models.Q(command_type="station_create",result_type="station",result_id__isnull=False)|models.Q(command_type="add_selected_configuration",result_type="configuration_batch",result_id__isnull=True)),name="cst_693_receipt_pair_shape")), migrations.AddIndex(model_name="configurationcommandreceipt",index=models.Index(fields=["workshop","-committed_at","-id"],name="idx_125_configuration_receipt")),
        migrations.RunSQL(GUARDS, REVERSE_GUARDS),
        migrations.RunPython(seed_material_category, migrations.RunPython.noop),
    ]
# fmt: on
