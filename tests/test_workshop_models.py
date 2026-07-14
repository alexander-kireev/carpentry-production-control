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
    # These rows model the D0-3 system sentinels that exist before a Workshop.
    station_cat = StationCategory.objects.create(
        workshop=None, name="undefined", colour="#cccccc"
    )
    material_cat = MaterialCategory.objects.create(workshop=None, name="undefined")
    role = WorkshopRole.objects.create(workshop=None, name="Admin")

    assert station_cat.workshop_id is None
    assert material_cat.workshop_id is None
    assert role.workshop_id is None


def test_distinct_null_workshop_names_coexist():
    # nulls_distinct=False only blocks a *duplicate* (workshop, name) — two
    # differently-named NULL-workshop sentinels are fine ("Admin" + "undefined").
    WorkshopRole.objects.create(workshop=None, name="Admin")
    WorkshopRole.objects.create(workshop=None, name="undefined")

    assert WorkshopRole.objects.filter(workshop__isnull=True).count() == 2


# --- nulls_distinct=False rejects a duplicate NULL-workshop row (the AC) ---


def test_duplicate_null_workshop_role_rejected():
    WorkshopRole.objects.create(workshop=None, name="Admin")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkshopRole.objects.create(workshop=None, name="Admin")


def test_duplicate_null_workshop_material_category_rejected():
    MaterialCategory.objects.create(workshop=None, name="undefined")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MaterialCategory.objects.create(workshop=None, name="undefined")


def test_duplicate_null_workshop_station_category_name_rejected():
    StationCategory.objects.create(workshop=None, name="undefined", colour="#111111")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            # Same name, different colour → still blocked by the name constraint.
            StationCategory.objects.create(
                workshop=None, name="undefined", colour="#222222"
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
