"""Workshop model + the sentinel-enabling workshop-FK behaviour (D0-1).

Covers the Workshop entity itself and the nullable ``workshop`` FK on the three
sentinel-bearing types (StationCategory, MaterialCategory, WorkshopRole), whose
per-workshop unique constraints use ``nulls_distinct=False`` so a duplicate
NULL-workshop system row is rejected at the DB (the D0-3 seed backstop / KI-012).
"""

import pytest
from django.db import IntegrityError, transaction

from catalog.models import (
    MaterialCategory,
    OperationType,
    StationCategory,
    WorkshopRole,
)
from tests.factories import WorkshopFactory

pytestmark = pytest.mark.django_db


def test_workshop_factory_is_valid_and_sets_created_at():
    workshop = WorkshopFactory()
    assert workshop.pk is not None
    assert workshop.created_at is not None


def test_workshop_str_is_name():
    workshop = WorkshopFactory(name="Bench & Board")
    assert str(workshop) == "Bench & Board"


# --- Sentinel-bearing types accept a NULL workshop (seeded before any Workshop) ---


def test_sentinel_types_allow_null_workshop():
    # The three sentinel-bearing types accept a NULL workshop — the mechanism
    # D0-3 relies on to seed workshop-independent rows. Synthetic names keep this
    # decoupled from the actual seeded sentinels (see test_system_seeds).
    station_cat = StationCategory.objects.create(
        workshop=None, name="null-ws-station-cat", colour="#cccccc"
    )
    material_cat = MaterialCategory.objects.create(
        workshop=None, name="null-ws-material-cat"
    )
    role = WorkshopRole.objects.create(workshop=None, name="null-ws-role")

    assert station_cat.workshop_id is None
    assert material_cat.workshop_id is None
    assert role.workshop_id is None


def test_distinct_null_workshop_names_coexist():
    # nulls_distinct=False only blocks a *duplicate* (workshop, name) — two
    # differently-named NULL-workshop rows coexist. Synthetic names, asserted by
    # name so the seeded sentinels don't perturb the count.
    WorkshopRole.objects.create(workshop=None, name="null-ws-role-a")
    WorkshopRole.objects.create(workshop=None, name="null-ws-role-b")

    assert (
        WorkshopRole.objects.filter(
            workshop__isnull=True, name__in=["null-ws-role-a", "null-ws-role-b"]
        ).count()
        == 2
    )


# --- nulls_distinct=False rejects a duplicate NULL-workshop row (the AC) ---


def test_duplicate_null_workshop_role_rejected():
    WorkshopRole.objects.create(workshop=None, name="null-ws-dup-role")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkshopRole.objects.create(workshop=None, name="null-ws-dup-role")


def test_duplicate_null_workshop_material_category_rejected():
    MaterialCategory.objects.create(workshop=None, name="null-ws-dup-mc")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MaterialCategory.objects.create(workshop=None, name="null-ws-dup-mc")


def test_duplicate_null_workshop_station_category_name_rejected():
    StationCategory.objects.create(
        workshop=None, name="null-ws-dup-sc", colour="#111111"
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            # Same name, different colour → still blocked by the name constraint.
            StationCategory.objects.create(
                workshop=None, name="null-ws-dup-sc", colour="#222222"
            )


def test_duplicate_null_workshop_station_category_colour_rejected():
    StationCategory.objects.create(workshop=None, name="alpha", colour="#abcdef")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            # Same colour, different name → blocked by the colour constraint.
            StationCategory.objects.create(
                workshop=None, name="beta", colour="#abcdef"
            )


# --- Non-sentinel workshop-scoped models require a workshop (non-null FK) ---


def test_operation_type_requires_workshop():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OperationType.objects.create(workshop=None, name="Cutting")
