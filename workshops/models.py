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


class Station(models.Model):
    class LifecycleStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    class AvailabilityStatus(models.TextChoices):
        AVAILABLE = "available", "Available"
        OFFLINE = "offline", "Offline"
        BROKEN = "broken", "Broken"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="stations"
    )
    code = models.TextField()
    name = models.TextField()
    lifecycle_status = models.TextField(
        choices=LifecycleStatus.choices,
        default=LifecycleStatus.ACTIVE,
        db_default="active",
    )
    availability_status = models.TextField(
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.AVAILABLE,
        db_default="available",
    )
    version = models.PositiveIntegerField(default=1, db_default=1)
    supported_operation_types = models.ManyToManyField(
        OperationType,
        through="StationSupportedOperationType",
        related_name="supporting_stations",
    )

    class Meta:
        db_table = "station"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=""), name="cst_sc03_station_name_nonblank"
            ),
            models.CheckConstraint(
                condition=models.Q(lifecycle_status__in=("active", "retired"))
                & models.Q(availability_status__in=("available", "offline", "broken"))
                & models.Q(version__gt=0),
                name="cst_073_station_state_version",
            ),
            models.CheckConstraint(
                condition=~models.Q(lifecycle_status="retired")
                | models.Q(availability_status="offline"),
                name="cst_072_station_retired_offline",
            ),
            models.UniqueConstraint(
                fields=("workshop", "code"), name="cst_071_station_code_uniq"
            ),
            models.UniqueConstraint(
                Lower("name"),
                "workshop",
                condition=models.Q(lifecycle_status="active"),
                name="cst_070_station_active_name_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("workshop", "lifecycle_status"),
                name="idx_023_station_scope",
            )
        ]


class StationSupportedOperationType(models.Model):
    pk = models.CompositePrimaryKey("station_id", "operation_type_id")
    station = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="supported_operation_links"
    )
    operation_type = models.ForeignKey(
        OperationType, on_delete=models.RESTRICT, related_name="station_support_links"
    )

    class Meta:
        db_table = "station_supported_operation_type"
        indexes = [
            models.Index(fields=("operation_type",), name="idx_031_station_support")
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


class Material(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="materials"
    )
    name = models.TextField()
    category = models.ForeignKey(
        MaterialCategory, on_delete=models.RESTRICT, related_name="materials"
    )
    unit = models.ForeignKey(
        UnitType, on_delete=models.RESTRICT, related_name="materials"
    )
    status = models.TextField(
        choices=Status.choices, default=Status.ACTIVE, db_default="active"
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "material"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=""), name="cst_313_material_name_nonblank"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=("active", "archived")),
                name="cst_314_material_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="cst_315_material_version"
            ),
            models.UniqueConstraint(
                Lower("name"), "workshop", name="cst_316_material_name_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=("workshop", "status"), name="idx_069_material_scope"),
            models.Index(fields=("unit",), name="idx_070_material_unit"),
            models.Index(fields=("category",), name="idx_071_material_category"),
        ]


class MaterialVariant(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="material_variants"
    )
    material = models.ForeignKey(
        Material, on_delete=models.RESTRICT, related_name="variants"
    )
    spec_label = models.TextField()
    current_stock = models.DecimalField(
        max_digits=14, decimal_places=4, default=0, db_default=0
    )
    min_threshold = models.DecimalField(max_digits=14, decimal_places=4)
    status = models.TextField(
        choices=Status.choices, default=Status.ACTIVE, db_default="active"
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "material_variant"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(spec_label=""),
                name="cst_324_material_variant_label",
            ),
            models.UniqueConstraint(
                Lower("spec_label"),
                "material",
                name="cst_325_material_variant_label_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(current_stock__gte=0),
                name="cst_326_material_variant_stock",
            ),
            models.CheckConstraint(
                condition=models.Q(min_threshold__gte=0),
                name="cst_327_material_variant_threshold",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=("active", "archived")),
                name="cst_328_material_variant_status",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="cst_329_material_variant_version",
            ),
        ]
        indexes = [
            models.Index(
                fields=("material", "status"), name="idx_072_variant_material"
            ),
            models.Index(
                fields=("workshop", "status"), name="idx_073_variant_workshop"
            ),
        ]


class StockEffect(models.Model):
    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="stock_effects"
    )
    material_variant = models.ForeignKey(
        MaterialVariant, on_delete=models.RESTRICT, related_name="stock_effects"
    )
    effect_type = models.TextField()
    source_type = models.TextField()
    command_identity = models.TextField()
    correlation_identity = models.TextField()
    source_identity = models.BigIntegerField(null=True, blank=True)
    source_version = models.IntegerField(null=True, blank=True)
    actor_or_system = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="stock_effects",
    )
    delta = models.DecimalField(max_digits=14, decimal_places=4)
    balance_before = models.DecimalField(max_digits=14, decimal_places=4)
    balance_after = models.DecimalField(max_digits=14, decimal_places=4)
    reason = models.TextField(null=True, blank=True)
    category = models.TextField(null=True, blank=True)
    stock_projection_version = models.PositiveIntegerField()
    accepted_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "stock_effect"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    effect_type__in=(
                        "opening_balance",
                        "operation_consumption",
                        "purchase_order_arrival",
                        "stock_write_off",
                        "manual_adjustment",
                    )
                ),
                name="cst_335_stock_effect_type",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    source_type__in=(
                        "material_variant_creation",
                        "operation_material_settlement",
                        "purchase_order_arrival",
                        "stock_write_off",
                        "manual_adjustment",
                    )
                ),
                name="cst_336_stock_effect_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        effect_type="opening_balance",
                        source_type="material_variant_creation",
                    )
                    | models.Q(
                        effect_type="operation_consumption",
                        source_type="operation_material_settlement",
                    )
                    | models.Q(
                        effect_type="purchase_order_arrival",
                        source_type="purchase_order_arrival",
                    )
                    | models.Q(
                        effect_type="stock_write_off",
                        source_type="stock_write_off",
                    )
                    | models.Q(
                        effect_type="manual_adjustment",
                        source_type="manual_adjustment",
                    )
                ),
                name="cst_337_stock_effect_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(source_version__isnull=True)
                | models.Q(source_version__gte=0),
                name="cst_340_stock_effect_source_version",
            ),
            models.CheckConstraint(
                condition=models.Q(actor_or_system__isnull=False)
                | models.Q(effect_type="purchase_order_arrival"),
                name="cst_341_stock_effect_actor",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        effect_type__in=("operation_consumption", "stock_write_off"),
                        delta__lt=0,
                    )
                    | models.Q(effect_type="purchase_order_arrival", delta__gt=0)
                    | models.Q(effect_type="opening_balance", delta__gte=0)
                    | models.Q(effect_type="manual_adjustment") & ~models.Q(delta=0)
                ),
                name="cst_342_stock_effect_sign",
            ),
            models.CheckConstraint(
                condition=models.Q(balance_before__gte=0),
                name="cst_343_stock_effect_before",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    balance_after=models.F("balance_before") + models.F("delta")
                )
                & models.Q(balance_after__gte=0),
                name="cst_344_stock_effect_balance",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(effect_type__in=("stock_write_off", "manual_adjustment"))
                    & models.Q(reason__isnull=False)
                    & ~models.Q(reason="")
                )
                | (
                    ~models.Q(effect_type__in=("stock_write_off", "manual_adjustment"))
                    & models.Q(reason__isnull=True)
                ),
                name="cst_345_stock_effect_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(stock_projection_version__gt=0),
                name="cst_347_stock_effect_projection_version",
            ),
        ]
        indexes = [
            models.Index(
                fields=("material_variant", "accepted_at"),
                name="idx_074_effect_variant_time",
            ),
            models.Index(
                fields=("source_type", "source_identity"),
                condition=models.Q(source_identity__isnull=False),
                name="idx_075_effect_source",
            ),
            models.Index(
                fields=("correlation_identity",), name="idx_076_effect_correlation"
            ),
        ]


class MaterialCommandReceipt(models.Model):
    workshop = models.ForeignKey(
        Workshop, on_delete=models.RESTRICT, related_name="material_receipts"
    )
    target_type = models.TextField()
    target_id = models.BigIntegerField()
    actor_user = models.ForeignKey(
        "identity.User",
        on_delete=models.RESTRICT,
        related_name="material_receipts",
    )
    idempotency_key = models.TextField()
    command_family = models.TextField()
    request_fingerprint = models.TextField()
    result_version = models.PositiveIntegerField()
    result_summary = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "material_command_receipt"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_type__in=("material", "material_variant")),
                name="cst_362_material_receipt_target",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    command_family__in=("edit", "archive", "restore", "manual_count")
                ),
                name="cst_364_material_receipt_family",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        command_family="manual_count", target_type="material_variant"
                    )
                    | ~models.Q(command_family="manual_count")
                    & models.Q(target_type__in=("material", "material_variant"))
                ),
                name="cst_365_material_receipt_pair",
            ),
            models.UniqueConstraint(
                fields=("workshop", "actor_user", "idempotency_key"),
                name="cst_366_material_receipt_key_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("target_type", "target_id"), name="idx_078_material_receipt"
            )
        ]
