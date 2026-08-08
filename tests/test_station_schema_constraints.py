import pytest
from django.db import IntegrityError, connection, transaction

from tests.test_library_commands import library_admin
from workshops.models import OperationType, Station, StationSupportedOperationType

pytestmark = pytest.mark.django_db(transaction=True)


def test_station_constraints_and_composite_capability_key():
    _, workshop = library_admin("station-schema")
    first = Station.objects.create(workshop=workshop, code="ST-001", name="Cell")
    with pytest.raises(IntegrityError), transaction.atomic():
        Station.objects.create(workshop=workshop, code="ST-002", name="cell")
    first.lifecycle_status = "retired"
    first.availability_status = "offline"
    first.save(update_fields=["lifecycle_status", "availability_status"])
    Station.objects.create(workshop=workshop, code="ST-002", name="cell")
    with pytest.raises(IntegrityError), transaction.atomic():
        Station.objects.create(
            workshop=workshop,
            code="ST-003",
            name="Bad",
            lifecycle_status="retired",
            availability_status="available",
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_index WHERE indrelid='station_supported_operation_type'::regclass AND indisprimary"
        )
        assert cursor.fetchone()[0] == 1


def test_capability_trigger_rejects_nonproduction_cross_tenant_and_retired():
    _, workshop = library_admin("station-trigger")
    _, foreign = library_admin("station-trigger-foreign")
    station = Station.objects.create(workshop=workshop, code="ST-001", name="Cell")
    for operation_type in (
        OperationType.objects.create(
            workshop=workshop,
            name="Planning",
            is_production=False,
            requires_clearance=False,
        ),
        OperationType.objects.create(
            workshop=foreign,
            name="Foreign",
            is_production=True,
            requires_clearance=True,
        ),
        OperationType.objects.create(
            workshop=workshop,
            name="Retired",
            is_production=True,
            requires_clearance=True,
            status="retired",
        ),
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            StationSupportedOperationType.objects.create(
                station=station, operation_type=operation_type
            )
