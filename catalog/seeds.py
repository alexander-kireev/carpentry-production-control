"""System-record seeds (D0-3).

The permanent, workshop-independent sentinel rows that must exist before any
Workshop: the "undefined" StationCategory / MaterialCategory / WorkshopRole
sentinels and the "Admin" WorkshopRole. They are seeded by an idempotent data
migration (``catalog/migrations/0002_system_seeds.py``), not a factory, because
they are system-owned records rather than test fixtures.

The logic lives here rather than inlined in the migration so the idempotency
contract is directly unit-testable, and so the later slices that consume these
reserved rows — A1 admin self-registration assigns "Admin"; the C2 guard
reserves "undefined" — share one source of truth for the names instead of
re-hardcoding the strings.

All four carry a NULL ``workshop`` FK (D0-1 made those three types' FK nullable,
KI-012) so they predate the first Workshop. ``get_or_create`` keyed on
``(workshop=None, name)`` is idempotent; the D0-1 ``nulls_distinct=False``
per-workshop unique constraints reject a duplicate NULL-workshop sentinel at the
DB as well.
"""

# Reserved system-record names. "undefined" (lower-case) is the cascade-target
# sentinel shared by all three types; "Admin" (capitalised) is the admin-only
# role. Casing matches the domain object specs.
UNDEFINED_NAME = "undefined"
ADMIN_ROLE_NAME = "Admin"

# Reserved colour for the "undefined" StationCategory. ``colour`` is required and
# unique-per-workshop, so the sentinel needs a stable, neutral swatch: Bootstrap's
# --bs-secondary / $gray-600, the muted "unassigned" grey in this stack. At the DB
# level it only has to be unique among NULL-workshop rows (it trivially is);
# reserving it from workshops' own palettes is an app-level guard for a later slice.
UNDEFINED_STATION_COLOUR = "#6C757D"


def seed_system_records(apps):
    """Create the four permanent system rows if absent. Idempotent.

    ``apps`` is the migration's historical registry (or the live ``apps`` in
    tests); models are resolved through it so this is safe to call from
    ``RunPython`` regardless of later model changes.
    """
    StationCategory = apps.get_model("catalog", "StationCategory")
    MaterialCategory = apps.get_model("catalog", "MaterialCategory")
    WorkshopRole = apps.get_model("catalog", "WorkshopRole")

    StationCategory.objects.get_or_create(
        workshop=None,
        name=UNDEFINED_NAME,
        defaults={"colour": UNDEFINED_STATION_COLOUR},
    )
    MaterialCategory.objects.get_or_create(workshop=None, name=UNDEFINED_NAME)
    WorkshopRole.objects.get_or_create(workshop=None, name=UNDEFINED_NAME)
    WorkshopRole.objects.get_or_create(workshop=None, name=ADMIN_ROLE_NAME)


def unseed_system_records(apps):
    """Reverse of :func:`seed_system_records` — remove the four system rows.

    Deletes only the NULL-workshop sentinels. If application data still
    references them (``User.workshop_role`` / ``Station.category`` /
    ``Material.category`` are PROTECT), the delete raises — correct: the
    dependents must be removed first. In practice this only runs when unwinding
    the migration on an otherwise-empty setup spine.
    """
    StationCategory = apps.get_model("catalog", "StationCategory")
    MaterialCategory = apps.get_model("catalog", "MaterialCategory")
    WorkshopRole = apps.get_model("catalog", "WorkshopRole")

    StationCategory.objects.filter(
        workshop__isnull=True, name=UNDEFINED_NAME
    ).delete()
    MaterialCategory.objects.filter(
        workshop__isnull=True, name=UNDEFINED_NAME
    ).delete()
    WorkshopRole.objects.filter(
        workshop__isnull=True, name__in=[UNDEFINED_NAME, ADMIN_ROLE_NAME]
    ).delete()
