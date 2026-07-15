"""Workshop singleton and the nine library/reference models (D0-1).

This introduces the catalog: the ``Workshop`` entity plus the reference types
the admin populates by import in Slice C. No seed rows are created here (D0-3)
and no ``User`` changes are made (D0-2). Deferred Phase-2 reverse relations
(Operation/Order/Shift) are intentionally not modelled.

Three sentinel-bearing types (StationCategory, MaterialCategory, WorkshopRole)
carry a *nullable* ``workshop`` FK so D0-3 can seed their workshop-independent
"undefined"/"Admin" system rows before any Workshop exists (KI-012); their
per-workshop unique constraints use ``nulls_distinct=False`` (Django 5.2 /
PostgreSQL 16) so a duplicate NULL-workshop system row is rejected at the DB.
"""

from django.db import models


class Workshop(models.Model):
    """The workshop itself — a singleton in MVP (one row per instance).

    The single-row *guard* is enforced in Slice A (registration/setup), not by a
    DB constraint here.
    """

    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class OperationType(models.Model):
    """A type of operation (Cutting, Assembly, Build Planning, ...)."""

    workshop = models.ForeignKey(
        Workshop, on_delete=models.CASCADE, related_name="operation_types"
    )
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    # True for floor-level types; False for administrative types (e.g. Build
    # Planning). Only production types appear in Station.supported_operations.
    is_production = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_operationtype_workshop_name",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class UnitType(models.Model):
    """A unit of measure (Piece/pc, Metre/m, Kilogram/kg, ...)."""

    workshop = models.ForeignKey(
        Workshop, on_delete=models.CASCADE, related_name="unit_types"
    )
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=16)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_unittype_workshop_name",
            ),
            models.UniqueConstraint(
                fields=["workshop", "abbreviation"],
                name="uq_unittype_workshop_abbreviation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.abbreviation})"


class StationCategory(models.Model):
    """Station grouping with a distinguishing colour on the schedule board.

    ``workshop`` is nullable so D0-3 can seed the workshop-independent
    "undefined" sentinel before any Workshop exists (KI-012).
    """

    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        related_name="station_categories",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    colour = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_stationcategory_workshop_name",
                nulls_distinct=False,
            ),
            models.UniqueConstraint(
                fields=["workshop", "colour"],
                name="uq_stationcategory_workshop_colour",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return self.name


class MaterialCategory(models.Model):
    """Material grouping.

    ``workshop`` is nullable so D0-3 can seed the workshop-independent
    "undefined" sentinel before any Workshop exists (KI-012).
    """

    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        related_name="material_categories",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_materialcategory_workshop_name",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ShiftDefinition(models.Model):
    """A shift template: a named time window running on a set of weekdays."""

    class Day(models.TextChoices):
        MON = "mon", "Monday"
        TUE = "tue", "Tuesday"
        WED = "wed", "Wednesday"
        THU = "thu", "Thursday"
        FRI = "fri", "Friday"
        SAT = "sat", "Saturday"
        SUN = "sun", "Sunday"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.CASCADE, related_name="shift_definitions"
    )
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    days = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_shiftdefinition_workshop_name",
            ),
            # The (start, end, days) window is unique regardless of name. days is
            # canonicalised in save() so an unordered day set counts as equal.
            models.UniqueConstraint(
                fields=["workshop", "start_time", "end_time", "days"],
                name="uq_shiftdefinition_workshop_window",
            ),
        ]

    def save(self, *args, **kwargs):
        # Canonicalise days to weekday order (deduped) so the (start, end, days)
        # uniqueness constraint treats an unordered day set as a duplicate.
        if self.days:
            canonical = list(self.Day.values)
            deduped = dict.fromkeys(self.days)
            self.days = sorted(
                deduped,
                key=lambda d: canonical.index(d) if d in canonical else len(canonical),
            )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class WorkshopRole(models.Model):
    """A workshop role template; ``default_clearances`` prefills the Add-User form.

    ``workshop`` is nullable so D0-3 can seed the workshop-independent
    "undefined" and "Admin" sentinels before any Workshop exists (KI-012). The
    "undefined" reservation is an app-level guard (C2), not a DB constraint.
    """

    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        related_name="workshop_roles",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    default_clearances = models.ManyToManyField(
        OperationType, blank=True, related_name="default_in_roles"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_workshoprole_workshop_name",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Station(models.Model):
    """A workshop station. Carries a system-assigned ST-NNN business id."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SCHEDULED_FOR_MAINT = "scheduled_for_maint", "Scheduled for maintenance"
        UNDER_MAINTENANCE = "under_maintenance", "Under maintenance"
        BROKEN = "broken", "Broken"
        OFFLINE = "offline", "Offline"
        RETIRED = "retired", "Retired"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.CASCADE, related_name="stations"
    )
    # Business id, format ST-NNN, system-assigned on create (see save()). The
    # integer PK stays the internal id; this is the user-facing reference.
    code = models.CharField(max_length=16, unique=True, blank=True)
    name = models.CharField(max_length=100)
    # Never NULL. PROTECT here; the domain's "reassign to the undefined sentinel
    # on category delete" is app-level logic in a later slice, not a DB cascade.
    category = models.ForeignKey(
        StationCategory, on_delete=models.PROTECT, related_name="stations"
    )
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.ACTIVE
    )
    supported_operations = models.ManyToManyField(
        OperationType, blank=True, related_name="stations"
    )
    maint_start = models.DateField(null=True, blank=True)
    maint_end = models.DateField(null=True, blank=True)
    maint_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_station_workshop_name",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._next_code()
        super().save(*args, **kwargs)

    @classmethod
    def _next_code(cls) -> str:
        """Next sequential ST-NNN from the current maximum.

        MVP import (Slice C) is serial, so a simple max+1 is sufficient; this is
        not concurrency-hardened. The ``code`` unique constraint is the backstop.
        """
        highest = 0
        for code in cls.objects.filter(code__startswith="ST-").values_list(
            "code", flat=True
        ):
            try:
                highest = max(highest, int(code.rsplit("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return f"ST-{highest + 1:03d}"

    def __str__(self) -> str:
        return f"{self.code} {self.name}".strip()


class Material(models.Model):
    """A catalogued material. May have zero variants (D-121 / KI-011)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    workshop = models.ForeignKey(
        Workshop, on_delete=models.CASCADE, related_name="materials"
    )
    name = models.CharField(max_length=100)
    # Never NULL; PROTECT (see Station.category note).
    category = models.ForeignKey(
        MaterialCategory, on_delete=models.PROTECT, related_name="materials"
    )
    # The unit of every variant's stock quantities (current_stock, reserved,
    # min_threshold, lot_sizes) — one unit per Material, shared by all variants.
    # A variant's spec_label is a free-text spec (e.g. "2000x150x50"), not a unit.
    unit = models.ForeignKey(
        UnitType, on_delete=models.PROTECT, related_name="materials"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "name"],
                name="uq_material_workshop_name",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class MaterialVariant(models.Model):
    """A specific variant of a Material. ``available``/``stock_status`` are derived."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class StockStatus(models.TextChoices):
        CLEAR = "clear", "Clear"
        LOW = "low", "Low"
        OUT = "out", "Out"

    material = models.ForeignKey(
        Material, on_delete=models.CASCADE, related_name="variants"
    )
    spec_label = models.CharField(max_length=100)
    # Quantities are in the parent Material's unit. Decimal (not float) so
    # reservation/stock arithmetic stays exact. current_stock and min_threshold
    # are domain-Required and carry no model default (D-125 / D0-4): a silent 0
    # would mask missing data (e.g. min_threshold=0 "never flags low"). reserved
    # is system-maintained and legitimately starts at 0.
    current_stock = models.DecimalField(max_digits=12, decimal_places=3)
    reserved = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    min_threshold = models.DecimalField(max_digits=12, decimal_places=3)
    lot_sizes = models.JSONField(default=list)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["material", "spec_label"],
                name="uq_materialvariant_material_spec_label",
            ),
        ]

    @property
    def available(self):
        """current_stock - reserved; can be negative. Not used in stock_status (MVP)."""
        return self.current_stock - self.reserved

    @property
    def stock_status(self) -> str:
        """Derived clear/low/out from current_stock vs min_threshold — never stored.

        MVP is stock-vs-threshold only; the reservation-aware ``available``/
        ``critical`` variant is deferred post-MVP. ``== 0`` is checked first so an
        empty variant with ``min_threshold == 0`` reads ``out``, not ``clear``.
        """
        if self.current_stock == 0:
            return self.StockStatus.OUT
        if self.current_stock < self.min_threshold:
            return self.StockStatus.LOW
        return self.StockStatus.CLEAR

    def __str__(self) -> str:
        return f"{self.material.name} - {self.spec_label}"
