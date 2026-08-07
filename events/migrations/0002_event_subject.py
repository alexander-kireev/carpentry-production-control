import django.db.models.deletion
from django.db import migrations, models

FORWARD_SQL = r"""
CREATE FUNCTION public.sc01_event_subject_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    RAISE EXCEPTION 'event subjects are immutable' USING ERRCODE = '23514';
END;
$function$;

CREATE TRIGGER cst_692_event_subject_immutable
BEFORE UPDATE OR DELETE ON public.event_subject
FOR EACH ROW EXECUTE FUNCTION public.sc01_event_subject_immutable();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS cst_692_event_subject_immutable ON public.event_subject;
DROP FUNCTION IF EXISTS public.sc01_event_subject_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("events", "0001_event_notification_boundary")]

    operations = [
        migrations.CreateModel(
            name="EventSubject",
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
                ("subject_type", models.TextField()),
                ("subject_id", models.BigIntegerField()),
                ("subject_role", models.TextField()),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="subjects",
                        to="events.event",
                    ),
                ),
            ],
            options={
                "db_table": "event_subject",
                "indexes": [
                    models.Index(
                        fields=["subject_type", "subject_id", "-event"],
                        name="idx_126_event_subject_lookup",
                    ),
                    models.Index(fields=["event"], name="idx_127_event_subject_event"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "event",
                            "subject_type",
                            "subject_id",
                            "subject_role",
                        ),
                        name="cst_686_event_subject_identity_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("subject_id__gt", 0)),
                        name="cst_687_event_subject_id_positive",
                    ),
                ],
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
