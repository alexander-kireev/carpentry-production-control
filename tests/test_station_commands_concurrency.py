import threading

import pytest
from django.db import close_old_connections

from events.models import Event
from tests.test_library_commands import library_admin
from workshops.commands import (
    create_station,
    edit_station,
    retire_station,
    transition_library_item,
)
from workshops.models import (
    ConfigurationCommandReceipt,
    OperationType,
    Station,
    StationSupportedOperationType,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _operation_type(workshop, name="Cutting"):
    return OperationType.objects.create(
        workshop=workshop,
        name=name,
        is_production=True,
        requires_clearance=True,
    )


def _run_concurrently(*functions):
    barrier = threading.Barrier(len(functions))
    results = []
    errors = []

    def worker(function):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            results.append(function())
        except BaseException as error:  # surfaced in the test thread
            errors.append(error)
        finally:
            close_old_connections()

    threads = [
        threading.Thread(target=worker, args=(function,)) for function in functions
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    return results


def test_concurrent_creates_allocate_distinct_monotonic_codes():
    actor, workshop = library_admin("station-race")

    def create(index):
        return lambda: create_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            submission_key=f"key-{index}",
            data={"name": f"Cell {index}", "capability_ids": []},
        )

    results = _run_concurrently(create(1), create(2))
    assert sorted(result.code for result in results) == ["committed", "committed"]
    assert list(Station.objects.order_by("code").values_list("code", flat=True)) == [
        "ST-001",
        "ST-002",
    ]


def test_concurrent_same_key_create_commits_one_graph_and_recovers_one_result():
    actor, workshop = library_admin("station-same-key-race")
    operation_type = _operation_type(workshop)

    def create():
        return create_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            submission_key="same-key",
            data={"name": "Cell A", "capability_ids": [operation_type.id]},
        )

    results = _run_concurrently(create, create)
    assert sorted(result.code for result in results) == ["committed", "recovered"]
    assert {result.station_code for result in results} == {"ST-001"}
    assert Station.objects.count() == 1
    assert (
        ConfigurationCommandReceipt.objects.filter(
            command_type="station_create"
        ).count()
        == 1
    )
    assert Event.objects.filter(event_type="STATION_CREATED").count() == 1
    workshop.refresh_from_db()
    assert workshop.station_code_counter == 1


def test_concurrent_same_key_changed_payload_commits_one_and_rejects_the_other():
    actor, workshop = library_admin("station-same-key-changed-race")

    def create(name):
        return lambda: create_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            submission_key="same-key",
            data={"name": name, "capability_ids": []},
        )

    results = _run_concurrently(create("Cell A"), create("Cell B"))
    assert sorted(result.code for result in results) == ["committed", "unavailable"]
    assert Station.objects.count() == 1
    assert Station.objects.get().name in {"Cell A", "Cell B"}
    assert (
        ConfigurationCommandReceipt.objects.filter(
            command_type="station_create"
        ).count()
        == 1
    )
    assert Event.objects.filter(event_type="STATION_CREATED").count() == 1
    workshop.refresh_from_db()
    assert workshop.station_code_counter == 1


def test_concurrent_edit_and_retire_have_one_version_one_winner_and_no_torn_state():
    actor, workshop = library_admin("station-edit-retire-race")
    operation_type = _operation_type(workshop)
    created = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data={"name": "Cell A", "capability_ids": [operation_type.id]},
    )

    results = _run_concurrently(
        lambda: edit_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            station_code=created.station_code,
            expected_version=1,
            data={"name": "Cell Alpha", "capability_ids": [operation_type.id]},
        ),
        lambda: retire_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            station_code=created.station_code,
            expected_version=1,
        ),
    )
    station = Station.objects.get(pk=created.station_id)
    assert sum(result.code == "committed" for result in results) == 1
    assert station.version == 2
    if station.lifecycle_status == Station.LifecycleStatus.RETIRED:
        assert station.availability_status == Station.AvailabilityStatus.OFFLINE
        assert Event.objects.filter(event_type="STATION_RETIRED").count() == 1
        assert (
            Event.objects.filter(event_type="OPERATION_TYPE_CAPABILITY_LOST").count()
            == 1
        )
    else:
        assert station.name == "Cell Alpha"
        assert Event.objects.filter(event_type="STATION_UPDATED").count() == 1
        assert not Event.objects.filter(event_type="STATION_RETIRED").exists()


def test_concurrent_retire_rejects_the_loser_and_emits_final_support_loss_once():
    actor, workshop = library_admin("station-retire-race")
    operation_type = _operation_type(workshop)
    created = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data={"name": "Cell A", "capability_ids": [operation_type.id]},
    )

    def retire():
        return retire_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            station_code=created.station_code,
            expected_version=1,
        )

    results = _run_concurrently(retire, retire)
    assert sum(result.code == "committed" for result in results) == 1
    station = Station.objects.get(pk=created.station_id)
    assert (station.lifecycle_status, station.version) == (
        Station.LifecycleStatus.RETIRED,
        2,
    )
    assert Event.objects.filter(event_type="STATION_RETIRED").count() == 1
    assert (
        Event.objects.filter(event_type="OPERATION_TYPE_CAPABILITY_LOST").count() == 1
    )


def test_create_and_operation_type_retire_converge_without_retired_support():
    actor, workshop = library_admin("station-create-type-retire-race")
    operation_type = _operation_type(workshop)

    results = _run_concurrently(
        lambda: create_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            submission_key="create",
            data={"name": "Cell A", "capability_ids": [operation_type.id]},
        ),
        lambda: transition_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="operation_type",
            item_id=operation_type.id,
            expected_version=operation_type.version,
            action="retire",
        ),
    )
    operation_type.refresh_from_db()
    active_support = StationSupportedOperationType.objects.filter(
        operation_type=operation_type,
        station__lifecycle_status=Station.LifecycleStatus.ACTIVE,
    ).exists()
    assert not (
        operation_type.status == OperationType.Status.RETIRED and active_support
    )
    assert {result.code for result in results} <= {
        "committed",
        "invalid",
        "success",
        "unavailable",
    }


def test_edit_add_and_operation_type_retire_converge_without_retired_support():
    actor, workshop = library_admin("station-edit-type-retire-race")
    operation_type = _operation_type(workshop)
    station = Station.objects.create(workshop=workshop, code="ST-001", name="Cell A")

    _run_concurrently(
        lambda: edit_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            station_code=station.code,
            expected_version=1,
            data={"name": station.name, "capability_ids": [operation_type.id]},
        ),
        lambda: transition_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="operation_type",
            item_id=operation_type.id,
            expected_version=operation_type.version,
            action="retire",
        ),
    )
    operation_type.refresh_from_db()
    active_support = StationSupportedOperationType.objects.filter(
        operation_type=operation_type,
        station__lifecycle_status=Station.LifecycleStatus.ACTIVE,
    ).exists()
    assert not (
        operation_type.status == OperationType.Status.RETIRED and active_support
    )


def test_station_and_operation_type_retire_converge_and_loss_is_not_duplicated():
    actor, workshop = library_admin("station-type-retire-race")
    operation_type = _operation_type(workshop)
    created = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data={"name": "Cell A", "capability_ids": [operation_type.id]},
    )

    _run_concurrently(
        lambda: retire_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            station_code=created.station_code,
            expected_version=1,
        ),
        lambda: transition_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="operation_type",
            item_id=operation_type.id,
            expected_version=operation_type.version,
            action="retire",
        ),
    )
    station = Station.objects.get(pk=created.station_id)
    operation_type.refresh_from_db()
    assert station.lifecycle_status == Station.LifecycleStatus.RETIRED
    assert not StationSupportedOperationType.objects.filter(
        operation_type=operation_type,
        station__lifecycle_status=Station.LifecycleStatus.ACTIVE,
    ).exists()
    assert Event.objects.filter(event_type="STATION_RETIRED").count() == 1
    assert (
        Event.objects.filter(event_type="OPERATION_TYPE_CAPABILITY_LOST").count() <= 1
    )
