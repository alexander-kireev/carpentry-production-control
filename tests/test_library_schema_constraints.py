import pytest
from django.db import IntegrityError, connection, transaction

from tests.test_library_commands import library_admin
from tests.test_workshop_creation import ensure_protected_configuration
from workshops.models import (
    ConfigurationCommandReceipt,
    MaterialCategory,
    ShiftDefinition,
    UnitType,
)
from workshops.protected_configuration import resolve_protected_configuration

pytestmark = pytest.mark.django_db(transaction=True)


def test_material_category_sentinel_and_indexes_exist():
    ensure_protected_configuration()
    protected = resolve_protected_configuration()
    assert protected.undefined_material_category.name == "undefined"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename IN ('event_subject','configuration_command_receipt')"
        )
        names = {row[0] for row in cursor.fetchall()}
    assert {
        "idx_126_event_subject_lookup",
        "idx_127_event_subject_event",
        "idx_125_configuration_receipt",
    } <= names


def test_lifecycle_uniqueness_and_shift_day_canonicalization_are_database_backstops():
    _, workshop = library_admin("schema")
    UnitType.objects.create(
        workshop=workshop, name="Metres", abbreviation="m", status="retired"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        UnitType.objects.create(workshop=workshop, name="metres", abbreviation="metre")
    with pytest.raises(IntegrityError), transaction.atomic():
        ShiftDefinition.objects.create(
            workshop=workshop,
            name="Bad",
            start_time="06:00",
            end_time="14:00",
            days=[1, 0],
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MaterialCategory.objects.create(workshop=workshop, name="undefined")


def test_receipt_command_result_mapping_and_shapes_are_database_backstops():
    actor, workshop = library_admin("receipt-map")
    mappings = {
        "workshop_role_create": "workshop_role",
        "operation_type_create": "operation_type",
        "unit_type_create": "unit_type",
        "material_category_create": "material_category",
        "shift_definition_create": "shift_definition",
        "material_create": "material",
        "material_variant_create": "material_variant",
        "station_create": "station",
        "add_selected_configuration": "configuration_batch",
    }
    for index, (command_type, result_type) in enumerate(mappings.items(), start=1):
        is_batch = result_type == "configuration_batch"
        ConfigurationCommandReceipt.objects.create(
            workshop=workshop,
            actor_user=actor,
            command_type=command_type,
            submission_key=f"valid-{index}",
            payload_fingerprint=f"fingerprint-{index}",
            result_type=result_type,
            result_id=None if is_batch else index,
            result_summary={"items": []} if is_batch else {"id": index},
        )
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == len(
        mappings
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ConfigurationCommandReceipt.objects.create(
            workshop=workshop,
            actor_user=actor,
            command_type="unit_type_create",
            submission_key="mismatch",
            payload_fingerprint="mismatch",
            result_type="operation_type",
            result_id=1,
            result_summary={"id": 1},
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        ConfigurationCommandReceipt.objects.create(
            workshop=workshop,
            actor_user=actor,
            command_type="add_selected_configuration",
            submission_key="bad-batch",
            payload_fingerprint="bad-batch",
            result_type="configuration_batch",
            result_id=1,
            result_summary={"items": []},
        )
