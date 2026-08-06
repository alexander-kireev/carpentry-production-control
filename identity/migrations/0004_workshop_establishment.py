import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models

FORWARD_SQL = r"""
CREATE FUNCTION public.sb03_workshop_creation_receipt_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    RAISE EXCEPTION 'workshop creation receipt is immutable' USING ERRCODE = '23514';
END;
$function$;

CREATE TRIGGER cst_672_workshop_creation_receipt_immutable
BEFORE UPDATE OR DELETE ON public.workshop_creation_command_receipt
FOR EACH ROW EXECUTE FUNCTION public.sb03_workshop_creation_receipt_immutable();

ALTER TABLE public.workshop_creation_command_receipt
ADD CONSTRAINT cst_673_workshop_receipt_actor_fk
FOREIGN KEY (actor_user_id) REFERENCES public.user_account(id) ON DELETE RESTRICT;

ALTER TABLE public.workshop_creation_command_receipt
ADD CONSTRAINT cst_673_workshop_receipt_result_fk
FOREIGN KEY (result_workshop_id) REFERENCES public.workshop(id) ON DELETE RESTRICT;
"""

REVERSE_SQL = r"""
ALTER TABLE public.workshop_creation_command_receipt
DROP CONSTRAINT IF EXISTS cst_673_workshop_receipt_actor_fk;
ALTER TABLE public.workshop_creation_command_receipt
DROP CONSTRAINT IF EXISTS cst_673_workshop_receipt_result_fk;
DROP TRIGGER IF EXISTS cst_672_workshop_creation_receipt_immutable
ON public.workshop_creation_command_receipt;
DROP FUNCTION IF EXISTS public.sb03_workshop_creation_receipt_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0003_registration_access"),
        ("workshops", "0003_seed_protected_identities"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkshopCreationCommandReceipt",
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
                    "actor_user",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="workshop_creation_receipt",
                        to="identity.user",
                    ),
                ),
                (
                    "result_workshop",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="creation_receipt",
                        to="workshops.workshop",
                    ),
                ),
            ],
            options={
                "db_table": "workshop_creation_command_receipt",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("fingerprint_version__gt", 0)),
                        name="cst_672_workshop_fingerprint_version_positive",
                    ),
                    models.UniqueConstraint(
                        fields=("actor_user",),
                        name="cst_674_workshop_receipt_actor_uniq",
                    ),
                    models.UniqueConstraint(
                        fields=("result_workshop",),
                        name="cst_675_workshop_receipt_result_uniq",
                    ),
                ],
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
