"""System-record seeds (D0-3).

The data migration ``catalog/migrations/0002_system_seeds`` seeds four
permanent, workshop-independent rows before any Workshop exists. These assert
the rows are present, unique, and workshop-independent, that the "undefined"
StationCategory carries its reserved colour, that "Admin" is available for A1 to
assign at registration, and that re-running the seed is idempotent.

pytest-django applies migrations when building the test DB, so the seeded rows
are part of the committed baseline every test sees.
"""

import pytest
from django.apps import apps as global_apps

from catalog.models import MaterialCategory, StationCategory, WorkshopRole
from catalog.seeds import (
    ADMIN_ROLE_NAME,
    UNDEFINED_NAME,
    UNDEFINED_STATION_COLOUR,
    seed_system_records,
)

pytestmark = pytest.mark.django_db


def _sentinel_counts():
    """Counts of the four seeded system rows (all workshop-independent)."""
    return {
        "station_category": StationCategory.objects.filter(
            workshop__isnull=True, name=UNDEFINED_NAME
        ).count(),
        "material_category": MaterialCategory.objects.filter(
            workshop__isnull=True, name=UNDEFINED_NAME
        ).count(),
        "undefined_role": WorkshopRole.objects.filter(
            workshop__isnull=True, name=UNDEFINED_NAME
        ).count(),
        "admin_role": WorkshopRole.objects.filter(
            workshop__isnull=True, name=ADMIN_ROLE_NAME
        ).count(),
    }


def test_undefined_station_category_seeded():
    category = StationCategory.objects.get(
        workshop__isnull=True, name=UNDEFINED_NAME
    )
    assert category.workshop_id is None
    assert category.colour == UNDEFINED_STATION_COLOUR


def test_undefined_material_category_seeded():
    category = MaterialCategory.objects.get(
        workshop__isnull=True, name=UNDEFINED_NAME
    )
    assert category.workshop_id is None


def test_undefined_workshop_role_seeded():
    role = WorkshopRole.objects.get(workshop__isnull=True, name=UNDEFINED_NAME)
    assert role.workshop_id is None


def test_admin_role_seeded_and_available_for_registration():
    # The A1 acceptance criterion: "Admin" exists and is queryable before any
    # Workshop, so register_admin can assign it at self-registration.
    role = WorkshopRole.objects.get(workshop__isnull=True, name=ADMIN_ROLE_NAME)
    assert role.workshop_id is None


def test_exactly_one_of_each_system_row():
    assert _sentinel_counts() == {
        "station_category": 1,
        "material_category": 1,
        "undefined_role": 1,
        "admin_role": 1,
    }


def test_seed_is_idempotent():
    # Re-running the seed against the already-seeded baseline must create no
    # duplicates. Uses the live apps registry, mirroring a real re-apply.
    before = _sentinel_counts()
    seed_system_records(global_apps)
    seed_system_records(global_apps)
    after = _sentinel_counts()

    assert before == after == {
        "station_category": 1,
        "material_category": 1,
        "undefined_role": 1,
        "admin_role": 1,
    }
