"""The nine library/reference models (D0-1).

Uniqueness (per workshop; spec_label per Material), the derived
``available``/``stock_status`` boundaries, zero-variant Material (D-121),
ST-NNN business-id assignment, the M2M relations, and factory validity.
"""

import re
from datetime import time
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from catalog.models import Material, MaterialVariant, OperationType
from tests.factories import (
    MaterialCategoryFactory,
    MaterialFactory,
    MaterialVariantFactory,
    OperationTypeFactory,
    ShiftDefinitionFactory,
    StationCategoryFactory,
    StationFactory,
    UnitTypeFactory,
    WorkshopFactory,
    WorkshopRoleFactory,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Factories produce valid instances for every model
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "factory_cls",
    [
        OperationTypeFactory,
        UnitTypeFactory,
        StationCategoryFactory,
        MaterialCategoryFactory,
        ShiftDefinitionFactory,
        WorkshopRoleFactory,
        StationFactory,
        MaterialFactory,
        MaterialVariantFactory,
    ],
)
def test_factory_produces_valid_instance(factory_cls):
    obj = factory_cls()
    assert obj.pk is not None


# --------------------------------------------------------------------------- #
# Uniqueness — within Workshop (reusable across workshops)
# --------------------------------------------------------------------------- #


def test_operation_type_name_unique_within_workshop():
    workshop = WorkshopFactory()
    OperationTypeFactory(workshop=workshop, name="Cutting")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OperationTypeFactory(workshop=workshop, name="Cutting")


def test_operation_type_name_reusable_across_workshops():
    OperationTypeFactory(name="Cutting")  # own workshop
    OperationTypeFactory(name="Cutting")  # different workshop
    assert OperationType.objects.filter(name="Cutting").count() == 2


def test_unit_type_name_unique_within_workshop():
    workshop = WorkshopFactory()
    UnitTypeFactory(workshop=workshop, name="Piece", abbreviation="pc")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            UnitTypeFactory(workshop=workshop, name="Piece", abbreviation="ea")


def test_unit_type_abbreviation_unique_within_workshop():
    workshop = WorkshopFactory()
    UnitTypeFactory(workshop=workshop, name="Piece", abbreviation="pc")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            UnitTypeFactory(workshop=workshop, name="Each", abbreviation="pc")


def test_station_category_name_unique_within_workshop():
    workshop = WorkshopFactory()
    StationCategoryFactory(workshop=workshop, name="Saws", colour="#111111")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            StationCategoryFactory(workshop=workshop, name="Saws", colour="#222222")


def test_station_category_colour_unique_within_workshop():
    workshop = WorkshopFactory()
    StationCategoryFactory(workshop=workshop, name="Saws", colour="#111111")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            StationCategoryFactory(workshop=workshop, name="Routers", colour="#111111")


def test_material_category_name_unique_within_workshop():
    workshop = WorkshopFactory()
    MaterialCategoryFactory(workshop=workshop, name="Sheet goods")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MaterialCategoryFactory(workshop=workshop, name="Sheet goods")


def test_workshop_role_name_unique_within_workshop():
    workshop = WorkshopFactory()
    WorkshopRoleFactory(workshop=workshop, name="Bench Joiner")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkshopRoleFactory(workshop=workshop, name="Bench Joiner")


def test_station_name_unique_within_workshop():
    workshop = WorkshopFactory()
    StationFactory(workshop=workshop, name="Table Saw 1")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            StationFactory(workshop=workshop, name="Table Saw 1")


def test_material_name_unique_within_workshop():
    workshop = WorkshopFactory()
    MaterialFactory(workshop=workshop, name="Oakboard")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MaterialFactory(workshop=workshop, name="Oakboard")


def test_shift_definition_name_unique_within_workshop():
    workshop = WorkshopFactory()
    ShiftDefinitionFactory(
        workshop=workshop, name="Day", start_time=time(8, 0), end_time=time(16, 0),
        days=["mon"],
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ShiftDefinitionFactory(
                workshop=workshop, name="Day", start_time=time(9, 0),
                end_time=time(17, 0), days=["tue"],
            )


def test_shift_definition_window_unique_regardless_of_name_and_day_order():
    workshop = WorkshopFactory()
    ShiftDefinitionFactory(
        workshop=workshop, name="A", start_time=time(8, 0), end_time=time(16, 0),
        days=["fri", "mon", "wed"],
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            # Same window, days in a different order, different name → duplicate.
            ShiftDefinitionFactory(
                workshop=workshop, name="B", start_time=time(8, 0),
                end_time=time(16, 0), days=["mon", "wed", "fri"],
            )


def test_shift_definition_days_sorted_on_save():
    shift = ShiftDefinitionFactory(days=["fri", "mon", "wed"])
    shift.refresh_from_db()
    assert shift.days == ["mon", "wed", "fri"]


def test_material_variant_spec_label_unique_within_material():
    material = MaterialFactory()
    MaterialVariantFactory(material=material, spec_label="2000x150x50")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MaterialVariantFactory(material=material, spec_label="2000x150x50")


def test_material_variant_spec_label_reusable_across_materials():
    MaterialVariantFactory(material=MaterialFactory(), spec_label="2000x150x50")
    MaterialVariantFactory(material=MaterialFactory(), spec_label="2000x150x50")
    assert MaterialVariant.objects.filter(spec_label="2000x150x50").count() == 2


# --------------------------------------------------------------------------- #
# MaterialVariant derived values — available / stock_status
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "current, reserved, threshold, expected",
    [
        (Decimal("10"), Decimal("0"), Decimal("5"), "clear"),      # above threshold
        (Decimal("5"), Decimal("0"), Decimal("5"), "clear"),       # exactly at threshold
        (Decimal("5"), Decimal("5"), Decimal("5"), "clear"),       # available == 0, not critical
        (Decimal("3"), Decimal("0"), Decimal("5"), "low"),         # below threshold
        (Decimal("5"), Decimal("5"), Decimal("10"), "low"),        # available 0, below threshold
        (Decimal("5"), Decimal("8"), Decimal("5"), "critical"),    # available < 0
        (Decimal("10"), Decimal("15"), Decimal("5"), "critical"),  # negative even above threshold
    ],
)
def test_stock_status_boundaries(current, reserved, threshold, expected):
    variant = MaterialVariant(
        current_stock=current, reserved=reserved, min_threshold=threshold
    )
    assert variant.available == current - reserved
    assert variant.stock_status == expected


def test_available_can_be_negative():
    variant = MaterialVariant(
        current_stock=Decimal("2"), reserved=Decimal("5"), min_threshold=Decimal("1")
    )
    assert variant.available == Decimal("-3")
    assert variant.stock_status == "critical"


def test_stock_status_persists_correctly_on_saved_variant():
    variant = MaterialVariantFactory(
        current_stock=Decimal("1"), reserved=Decimal("0"), min_threshold=Decimal("5")
    )
    variant.refresh_from_db()
    assert variant.stock_status == "low"


# --------------------------------------------------------------------------- #
# Material may have zero variants (D-121 / KI-011)
# --------------------------------------------------------------------------- #


def test_material_valid_with_zero_variants():
    material = MaterialFactory()
    assert material.variants.count() == 0
    assert Material.objects.filter(pk=material.pk).exists()


def test_material_unit_applies_to_variant_quantities():
    # One unit per Material; the variant's numeric stock is in that unit.
    unit = UnitTypeFactory(name="Piece", abbreviation="pc")
    material = MaterialFactory(
        workshop=unit.workshop,
        category=MaterialCategoryFactory(workshop=unit.workshop),
        unit=unit,
        name="Oakboard",
    )
    variant = MaterialVariantFactory(
        material=material,
        spec_label="2000x150x50",
        current_stock=Decimal("10"),
        reserved=Decimal("0"),
        min_threshold=Decimal("10"),
    )
    assert variant.material.unit.abbreviation == "pc"
    assert variant.available == Decimal("10")
    assert variant.stock_status == "clear"


# --------------------------------------------------------------------------- #
# Station ST-NNN business id
# --------------------------------------------------------------------------- #


def test_station_code_assigned_on_create():
    station = StationFactory()
    assert re.fullmatch(r"ST-\d{3}", station.code)


def test_station_codes_increment():
    workshop = WorkshopFactory()
    first = StationFactory(workshop=workshop)
    second = StationFactory(workshop=workshop)
    assert int(second.code.split("-")[1]) == int(first.code.split("-")[1]) + 1


def test_station_code_stable_on_update():
    station = StationFactory()
    original = station.code
    station.name = "Renamed"
    station.save()
    station.refresh_from_db()
    assert station.code == original


# --------------------------------------------------------------------------- #
# Many-to-many relations
# --------------------------------------------------------------------------- #


def test_station_supported_operations_m2m():
    workshop = WorkshopFactory()
    ops = [OperationTypeFactory(workshop=workshop) for _ in range(2)]
    station = StationFactory(workshop=workshop, supported_operations=ops)
    assert station.supported_operations.count() == 2


def test_workshop_role_default_clearances_m2m():
    workshop = WorkshopFactory()
    ops = [OperationTypeFactory(workshop=workshop) for _ in range(2)]
    role = WorkshopRoleFactory(workshop=workshop, default_clearances=ops)
    assert role.default_clearances.count() == 2
