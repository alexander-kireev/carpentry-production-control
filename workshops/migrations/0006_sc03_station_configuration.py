import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Lower

CAPABILITY_GUARD = r"""
CREATE FUNCTION public.sc03_station_capability_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
  station_workshop bigint;
  type_workshop bigint;
  type_production boolean;
  type_key text;
  type_status text;
BEGIN
  SELECT workshop_id
    INTO station_workshop
    FROM public.station
   WHERE id = NEW.station_id;

  SELECT workshop_id, is_production, machine_key, status
    INTO type_workshop, type_production, type_key, type_status
    FROM public.operation_type
   WHERE id = NEW.operation_type_id;

  IF station_workshop IS NULL
     OR type_production IS DISTINCT FROM true
     OR NOT (
       (type_workshop = station_workshop AND type_status = 'active')
       OR (
         type_workshop IS NULL
         AND type_key = 'other'
         AND type_status = 'active'
       )
     ) THEN
    RAISE EXCEPTION 'invalid Station capability relationship'
      USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END
$function$;

CREATE TRIGGER cst_019_station_capability_guard
BEFORE INSERT OR UPDATE ON public.station_supported_operation_type
FOR EACH ROW
EXECUTE FUNCTION public.sc03_station_capability_guard();
"""

REVERSE_CAPABILITY_GUARD = r"""
DROP TRIGGER IF EXISTS cst_019_station_capability_guard
  ON public.station_supported_operation_type;
DROP FUNCTION IF EXISTS public.sc03_station_capability_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0002_event_subject"),
        ("workshops", "0005_sc02_material_catalogue"),
    ]

    operations = [
        migrations.CreateModel(
            name="Station",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("code", models.TextField()),
                ("name", models.TextField()),
                (
                    "lifecycle_status",
                    models.TextField(
                        choices=[("active", "Active"), ("retired", "Retired")],
                        db_default="active",
                        default="active",
                    ),
                ),
                (
                    "availability_status",
                    models.TextField(
                        choices=[
                            ("available", "Available"),
                            ("offline", "Offline"),
                            ("broken", "Broken"),
                        ],
                        db_default="available",
                        default="available",
                    ),
                ),
                ("version", models.PositiveIntegerField(db_default=1, default=1)),
                (
                    "workshop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="stations",
                        to="workshops.workshop",
                    ),
                ),
            ],
            options={
                "db_table": "station",
                "indexes": [
                    models.Index(
                        fields=["workshop", "lifecycle_status"],
                        name="idx_023_station_scope",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=~models.Q(name=""),
                        name="cst_sc03_station_name_nonblank",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(lifecycle_status__in=("active", "retired"))
                        & models.Q(
                            availability_status__in=(
                                "available",
                                "offline",
                                "broken",
                            )
                        )
                        & models.Q(version__gt=0),
                        name="cst_073_station_state_version",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(lifecycle_status="retired")
                        | models.Q(availability_status="offline"),
                        name="cst_072_station_retired_offline",
                    ),
                    models.UniqueConstraint(
                        fields=("workshop", "code"),
                        name="cst_071_station_code_uniq",
                    ),
                    models.UniqueConstraint(
                        Lower("name"),
                        "workshop",
                        condition=models.Q(lifecycle_status="active"),
                        name="cst_070_station_active_name_uniq",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="StationSupportedOperationType",
            fields=[
                (
                    "pk",
                    models.CompositePrimaryKey(
                        "station_id",
                        "operation_type_id",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "operation_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="station_support_links",
                        to="workshops.operationtype",
                    ),
                ),
                (
                    "station",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supported_operation_links",
                        to="workshops.station",
                    ),
                ),
            ],
            options={
                "db_table": "station_supported_operation_type",
                "indexes": [
                    models.Index(
                        fields=["operation_type"],
                        name="idx_031_station_support",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="station",
            name="supported_operation_types",
            field=models.ManyToManyField(
                related_name="supporting_stations",
                through="workshops.StationSupportedOperationType",
                to="workshops.operationtype",
            ),
        ),
        migrations.RunSQL(CAPABILITY_GUARD, REVERSE_CAPABILITY_GUARD),
    ]
