from django.contrib.postgres.fields import ArrayField
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


class UnitType(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="unit_types"
    )
    name = models.TextField()
    abbreviation = models.TextField()
    status = models.TextField(
        choices=Status.choices, default=Status.ACTIVE, db_default="active"
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "unit_type"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("active", "retired")),
                name="cst_050_unit_type_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="cst_051_unit_type_version"
            ),
            models.CheckConstraint(
                condition=~models.Q(name="") & ~models.Q(abbreviation=""),
                name="cst_052_unit_type_labels_nonempty",
            ),
            models.UniqueConstraint(
                Lower("name"), "workshop", name="cst_053_unit_type_name_uniq"
            ),
            models.UniqueConstraint(
                Lower("abbreviation"),
                "workshop",
                name="cst_054_unit_type_abbreviation_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=("workshop", "status"), name="idx_015_unit_scope")
        ]


class MaterialCategory(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    workshop = models.ForeignKey(
        Workshop,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="material_categories",
    )
    name = models.TextField()
    machine_key = models.TextField(null=True, blank=True)
    status = models.TextField(
        choices=Status.choices, default=Status.ACTIVE, db_default="active"
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "material_category"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("active", "retired")),
                name="cst_055_material_category_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="cst_056_material_category_version",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""), name="cst_057_material_category_name"
            ),
            models.CheckConstraint(
                condition=models.Q(machine_key__isnull=True)
                | models.Q(status="active"),
                name="cst_058_material_category_sentinel_active",
            ),
            models.CheckConstraint(
                condition=models.Q(workshop__isnull=True)
                | ~models.Q(name__iexact="undefined"),
                name="cst_059_material_category_reserved_name",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "workshop",
                condition=models.Q(workshop__isnull=False),
                name="cst_060_material_category_name_uniq",
            ),
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(workshop__isnull=True),
                name="cst_061_global_material_category_name_uniq",
            ),
            models.UniqueConstraint(
                fields=("machine_key",),
                condition=models.Q(workshop__isnull=True),
                name="cst_062_global_material_category_key_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=("workshop", "status"), name="idx_016_category_scope")
        ]


class ShiftDefinition(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="shift_definitions"
    )
    name = models.TextField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    days = ArrayField(models.SmallIntegerField())
    status = models.TextField(
        choices=Status.choices, default=Status.ACTIVE, db_default="active"
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "shift_definition"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("active", "retired")),
                name="cst_063_shift_definition_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="cst_064_shift_definition_version",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""), name="cst_065_shift_definition_name"
            ),
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F("end_time")),
                name="cst_066_shift_definition_same_day",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "workshop",
                condition=models.Q(status="active"),
                name="cst_067_shift_definition_active_name_uniq",
            ),
            models.UniqueConstraint(
                fields=("workshop", "start_time", "end_time", "days"),
                condition=models.Q(status="active"),
                name="cst_068_shift_definition_active_shape_uniq",
            ),
        ]


class WorkshopRoleDefaultClearance(models.Model):
    workshop_role = models.ForeignKey(
        WorkshopRole, on_delete=models.CASCADE, related_name="default_clearance_links"
    )
    operation_type = models.ForeignKey(
        OperationType, on_delete=models.RESTRICT, related_name="default_role_links"
    )

    class Meta:
        db_table = "workshop_role_default_clearance"
        constraints = [
            models.UniqueConstraint(
                fields=("workshop_role", "operation_type"),
                name="cst_069_workshop_role_clearance_uniq",
            )
        ]


class ConfigurationCommandReceipt(models.Model):
    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="configuration_receipts"
    )
    actor_user = models.ForeignKey(
        "identity.User",
        on_delete=models.RESTRICT,
        related_name="configuration_receipts",
    )
    command_type = models.TextField()
    submission_key = models.TextField()
    fingerprint_version = models.SmallIntegerField(default=1, db_default=1)
    payload_fingerprint = models.TextField()
    result_type = models.TextField()
    result_id = models.BigIntegerField(null=True, blank=True)
    result_summary = models.JSONField(default=dict, db_default={})
    state = models.TextField(default="committed", db_default="committed")
    committed_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "configuration_command_receipt"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fingerprint_version__gt=0),
                name="cst_683_configuration_fingerprint_version",
            ),
            models.CheckConstraint(
                condition=models.Q(state="committed"),
                name="cst_685_configuration_receipt_state",
            ),
            models.CheckConstraint(
                condition=models.Q(result_id__gt=0),
                name="cst_688_configuration_result_id_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        command_type="workshop_role_create",
                        result_type="workshop_role",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="operation_type_create",
                        result_type="operation_type",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="unit_type_create",
                        result_type="unit_type",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="material_category_create",
                        result_type="material_category",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="shift_definition_create",
                        result_type="shift_definition",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="material_create",
                        result_type="material",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="material_variant_create",
                        result_type="material_variant",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="station_create",
                        result_type="station",
                        result_id__isnull=False,
                    )
                    | models.Q(
                        command_type="add_selected_configuration",
                        result_type="configuration_batch",
                        result_id__isnull=True,
                    )
                ),
                name="cst_693_receipt_pair_shape",
            ),
            models.UniqueConstraint(
                fields=("workshop", "command_type", "submission_key"),
                name="cst_684_configuration_receipt_key_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("workshop", "-committed_at", "-id"),
                name="idx_125_configuration_receipt",
            )
        ]
