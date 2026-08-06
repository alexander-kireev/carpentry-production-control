from django.db import models
from django.db.models.expressions import RawSQL
from django.db.models.functions import Lower


class Workshop(models.Model):
    class Status(models.TextChoices):
        MANAGER_REQUIRED = "manager_required", "Manager required"
        MANAGER_ACTIVATION_PENDING = (
            "manager_activation_pending",
            "Manager activation pending",
        )
        OPERATIONAL = "operational", "Operational"

    name = models.TextField()
    address = models.TextField()
    email = models.TextField()
    timezone = models.TextField()
    status = models.TextField(
        choices=Status.choices,
        default=Status.MANAGER_REQUIRED,
        db_default="manager_required",
    )
    version = models.PositiveIntegerField(default=1, db_default=1)
    created_at = models.DateTimeField(auto_now_add=True, db_default=RawSQL("now()", ()))
    timezone_correction_idempotency_key = models.TextField(null=True, blank=True)
    station_code_counter = models.PositiveIntegerField(default=0, db_default=0)
    customer_code_counter = models.PositiveIntegerField(default=0, db_default=0)
    order_code_counter = models.PositiveIntegerField(default=0, db_default=0)
    build_code_counter = models.PositiveIntegerField(default=0, db_default=0)

    class Meta:
        db_table = "workshop"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "manager_required",
                        "manager_activation_pending",
                        "operational",
                    )
                ),
                name="cst_001_workshop_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="cst_003_workshop_version_positive",
            ),
            models.UniqueConstraint(
                fields=("timezone_correction_idempotency_key",),
                name="cst_006_workshop_timezone_key_uniq",
            ),
        ]


class WorkshopRole(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    workshop = models.ForeignKey(
        Workshop,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="roles",
    )
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    machine_key = models.TextField(null=True, blank=True)
    status = models.TextField(
        choices=Status.choices, default=Status.ACTIVE, db_default="active"
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "workshop_role"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("active", "retired")),
                name="cst_007_workshop_role_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="cst_008_workshop_role_version_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(machine_key__isnull=True)
                | models.Q(status="active"),
                name="cst_012_workshop_role_sentinel_active",
            ),
            models.CheckConstraint(
                condition=models.Q(workshop__isnull=True)
                | ~models.Q(name__iexact="undefined") & ~models.Q(name__iexact="admin"),
                name="cst_013_workshop_role_reserved_names",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "workshop",
                condition=models.Q(workshop__isnull=False),
                name="cst_009_workshop_role_name_uniq",
            ),
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(workshop__isnull=True),
                name="cst_010_global_workshop_role_name_uniq",
            ),
            models.UniqueConstraint(
                fields=("machine_key",), name="cst_011_workshop_role_key_uniq"
            ),
        ]
        indexes = [
            models.Index(
                fields=("workshop", "status"), name="idx_003_workshop_role_scope"
            )
        ]


class OperationType(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    workshop = models.ForeignKey(
        Workshop,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="operation_types",
    )
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    is_production = models.BooleanField()
    requires_clearance = models.BooleanField(default=True, db_default=True)
    machine_key = models.TextField(null=True, blank=True)
    first_referenced_at = models.DateTimeField(null=True, blank=True)
    status = models.TextField(
        choices=Status.choices, default=Status.ACTIVE, db_default="active"
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "operation_type"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("active", "retired")),
                name="cst_047_operation_type_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="cst_038_operation_type_version_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(machine_key__isnull=True)
                | models.Q(status="active"),
                name="cst_045_operation_type_protected_active",
            ),
            models.CheckConstraint(
                condition=models.Q(workshop__isnull=True)
                | ~models.Q(name__iexact="other"),
                name="cst_048_operation_type_other_reserved",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="cst_049_operation_type_name_nonempty",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "workshop",
                condition=models.Q(workshop__isnull=False),
                name="cst_041_operation_type_name_uniq",
            ),
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(workshop__isnull=True),
                name="cst_042_global_operation_type_name_uniq",
            ),
            models.UniqueConstraint(
                fields=("machine_key",),
                condition=models.Q(workshop__isnull=True),
                name="cst_043_global_operation_type_key_uniq",
            ),
            models.UniqueConstraint(
                fields=("workshop", "machine_key"),
                condition=models.Q(workshop__isnull=False, machine_key__isnull=False),
                name="cst_044_workshop_operation_type_key_uniq",
            ),
        ]
