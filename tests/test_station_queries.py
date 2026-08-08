import pytest

from tests.test_cross_role_visual_conformance import _operational_people
from workshops.models import OperationType, Station, StationSupportedOperationType
from workshops.queries import get_station_detail, get_stations_catalogue

pytestmark = pytest.mark.django_db(transaction=True)


def test_role_safe_query_filters_and_redacts_admin_fields():
    admin, manager, operator = _operational_people("station-query")
    operation_type = OperationType.objects.create(
        workshop=admin.workshop,
        name="Cutting",
        is_production=True,
        requires_clearance=True,
    )
    station = Station.objects.create(
        workshop=admin.workshop, code="ST-001", name="Saw Cell"
    )
    StationSupportedOperationType.objects.create(
        station=station, operation_type=operation_type
    )
    for viewer in (manager, operator):
        result = get_stations_catalogue(
            viewer,
            search="Cut",
            availability="available",
            capability_id=operation_type.id,
        )
        assert [row["code"] for row in result["stations"]] == ["ST-001"]
        assert (
            "version" not in result["stations"][0]
            and "can_edit" not in result["stations"][0]
        )
        assert get_station_detail(viewer, "ST-001")["station"]["name"] == "Saw Cell"
    assert get_stations_catalogue(admin)["stations"][0]["can_edit"] is True


def test_cross_tenant_station_detail_is_absent():
    admin, *_ = _operational_people("station-query-source")
    foreign, *_ = _operational_people("station-query-foreign")
    Station.objects.create(workshop=admin.workshop, code="ST-001", name="Hidden")
    assert get_station_detail(foreign, "ST-001") is None
