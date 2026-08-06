import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models

FORWARD_SQL = r"""
CREATE FUNCTION public.sb02_registration_receipt_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    RAISE EXCEPTION 'registration receipt is immutable' USING ERRCODE = '23514';
END;
$function$;

CREATE TRIGGER cst_669_registration_receipt_immutable
BEFORE UPDATE OR DELETE ON public.registration_command_receipt
FOR EACH ROW EXECUTE FUNCTION public.sb02_registration_receipt_immutable();

ALTER TABLE public.registration_command_receipt
ADD CONSTRAINT cst_669_registration_result_user_fk
FOREIGN KEY (result_user_id) REFERENCES public.user_account(id) ON DELETE RESTRICT;
"""

REVERSE_SQL = r"""
ALTER TABLE public.registration_command_receipt
DROP CONSTRAINT IF EXISTS cst_669_registration_result_user_fk;
DROP TRIGGER IF EXISTS cst_669_registration_receipt_immutable
ON public.registration_command_receipt;
DROP FUNCTION IF EXISTS public.sb02_registration_receipt_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("identity", "0002_database_guards")]

    operations = [
        migrations.CreateModel(
            name="RegistrationCommandReceipt",
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
                ("idempotency_key", models.TextField(unique=True)),
                ("fingerprint_version", models.SmallIntegerField()),
                ("payload_fingerprint", models.BinaryField()),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.expressions.RawSQL("now()", ())
                    ),
                ),
                (
                    "result_user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="registration_receipt",
                        to="identity.user",
                        db_constraint=False,
                    ),
                ),
            ],
            options={
                "db_table": "registration_command_receipt",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("fingerprint_version__gt", 0)),
                        name="cst_669_registration_fingerprint_version_positive",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ActivationCodeAttemptBucket",
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
                ("hmac_key_version", models.SmallIntegerField()),
                ("client_ip_hmac", models.BinaryField()),
                ("window_started_at", models.DateTimeField()),
                ("failed_attempt_count", models.SmallIntegerField()),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.expressions.RawSQL("now()", ())
                    ),
                ),
            ],
            options={
                "db_table": "activation_code_attempt_bucket",
                "indexes": [
                    models.Index(
                        fields=["window_started_at"], name="idx_124_activation_window"
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("hmac_key_version__gt", 0)),
                        name="cst_679_activation_hmac_version_positive",
                    ),
                    models.UniqueConstraint(
                        fields=("hmac_key_version", "client_ip_hmac"),
                        name="cst_680_activation_bucket_identity_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("failed_attempt_count__gte", 0),
                            ("failed_attempt_count__lte", 5),
                        ),
                        name="cst_681_activation_failure_count_range",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("updated_at__gte", models.F("window_started_at"))
                        ),
                        name="cst_681_activation_timestamps_ordered",
                    ),
                ],
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
