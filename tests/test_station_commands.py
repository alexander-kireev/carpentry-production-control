import pytest
from django.db import transaction
from django.utils import timezone

from events import producer as event_producer
from events.models import Event, EventNotificationIntent, EventSubject
from events.producer import EventSpec, EventSubjectSpec, produce_events
from events.recipient_policies import resolve_recipients
from tests.test_cross_role_visual_conformance import _operational_people
from tests.test_library_commands import library_admin
from workshops import commands as station_commands
from workshops import station_dependencies
from workshops.commands import create_station, edit_station, retire_station
from workshops.models import (
    ConfigurationCommandReceipt,
    OperationType,
    Station,
    StationSupportedOperationType,
)
from workshops.station_dependencies import StationBlocker

pytestmark = pytest.mark.django_db(transaction=True)


def _type(workshop, name="Cutting"):
    return OperationType.objects.create(
        workshop=workshop,
        name=name,
        description="",
        is_production=True,
        requires_clearance=True,
    )


def test_create_replay_zero_many_and_explicit_other():
    actor, workshop = library_admin("station-create")
    cutting = _type(workshop)
    other = OperationType.objects.get(machine_key="other")
    first = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="same",
        data={"name": "Cell A", "capability_ids": [cutting.id, other.id]},
    )
    replay = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="same",
        data={"name": "Cell A", "capability_ids": [other.id, cutting.id]},
    )
    assert (first.code, replay.code) == ("committed", "recovered")
    station = Station.objects.get(pk=first.station_id)
    assert station.code == "ST-001"
    assert set(station.supported_operation_types.values_list("id", flat=True)) == {
        cutting.id,
        other.id,
    }
    assert Station.objects.count() == 1
    assert (
        ConfigurationCommandReceipt.objects.filter(
            command_type="station_create"
        ).count()
        == 1
    )
    assert Event.objects.filter(event_type="STATION_CREATED").count() == 1

    empty = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="empty",
        data={"name": "Cell B", "capability_ids": []},
    )
    assert empty.station_code == "ST-002"
    assert not Station.objects.get(
        pk=empty.station_id
    ).supported_operation_types.exists()


def test_edit_and_terminal_retirement_emit_exact_history():
    actor, workshop = library_admin("station-edit")
    cutting = _type(workshop)
    result = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data={"name": "Cell A", "capability_ids": [cutting.id]},
    )
    edited = edit_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        station_code=result.station_code,
        expected_version=1,
        data={"name": "Cell Alpha", "capability_ids": []},
    )
    assert edited.code == "committed" and edited.version == 2
    assert Event.objects.filter(event_type="STATION_UPDATED").count() == 1
    assert (
        Event.objects.filter(event_type="OPERATION_TYPE_CAPABILITY_LOST").count() == 1
    )
    retired = retire_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        station_code=result.station_code,
        expected_version=2,
    )
    assert retired.code == "committed" and retired.version == 3
    station = Station.objects.get(pk=result.station_id)
    assert (station.lifecycle_status, station.availability_status) == (
        "retired",
        "offline",
    )
    assert (
        Event.objects.get(event_type="STATION_RETIRED").payload["prior_availability"]
        == "available"
    )


def test_stale_denial_and_changed_replay_are_silent():
    actor, workshop = library_admin("station-fail")
    result = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="key",
        data={"name": "Cell A", "capability_ids": []},
    )
    before = Event.objects.count()
    assert (
        create_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            submission_key="key",
            data={"name": "Changed", "capability_ids": []},
        ).code
        == "unavailable"
    )
    assert (
        edit_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            station_code=result.station_code,
            expected_version=999,
            data={"name": "Cell A", "capability_ids": []},
        ).code
        == "stale"
    )
    assert Event.objects.count() == before


@pytest.mark.parametrize(
    "malformed",
    [1.5, "1", "not-an-id", True, None],
)
def test_malformed_capability_identifiers_have_zero_effect(malformed):
    actor, workshop = library_admin(f"station-malformed-{type(malformed).__name__}")
    operation_type = _type(workshop)
    before = (
        workshop.station_code_counter,
        Station.objects.count(),
        ConfigurationCommandReceipt.objects.count(),
        Event.objects.count(),
    )
    result = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key=f"malformed-{type(malformed).__name__}",
        data={"name": "Cell A", "capability_ids": [malformed, operation_type.id]},
    )
    workshop.refresh_from_db()
    assert result.code == "invalid"
    assert (
        workshop.station_code_counter,
        Station.objects.count(),
        ConfigurationCommandReceipt.objects.count(),
        Event.objects.count(),
    ) == before


def test_station_recipient_policies_keep_retirement_and_loss_distinct():
    admin, manager, _ = _operational_people("station-routing")
    cutting = _type(admin.workshop)
    result = create_station(
        actor_id=admin.id,
        workshop_id=admin.workshop_id,
        submission_key="create",
        data={"name": "Cell A", "capability_ids": [cutting.id]},
    )
    retire_station(
        actor_id=admin.id,
        workshop_id=admin.workshop_id,
        station_code=result.station_code,
        expected_version=1,
    )
    retirement = Event.objects.get(event_type="STATION_RETIRED")
    loss = Event.objects.get(event_type="OPERATION_TYPE_CAPABILITY_LOST")
    assert [row.user_id for row in resolve_recipients(retirement)] == [manager.id]
    assert [row.user_id for row in resolve_recipients(loss)] == [manager.id]


@pytest.mark.parametrize(
    "fault_stage",
    [
        "station_insert",
        "capability_insert",
        "event",
        "intent",
        "event_subject",
        "receipt",
    ],
)
def test_late_create_faults_roll_back_counter_source_capability_event_and_receipt(
    monkeypatch, fault_stage
):
    actor, workshop = library_admin(f"station-fault-{fault_stage}")
    operation_type = _type(workshop)
    targets = {
        "station_insert": (Station.objects, "create"),
        "capability_insert": (StationSupportedOperationType.objects, "bulk_create"),
        "event": (station_commands, "_station_event"),
        "intent": (event_producer.EventNotificationIntent.objects, "create"),
        "event_subject": (event_producer.EventSubject.objects, "bulk_create"),
        "receipt": (ConfigurationCommandReceipt.objects, "create"),
    }
    owner, attribute = targets[fault_stage]
    original = getattr(owner, attribute)

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected late fault")

    monkeypatch.setattr(owner, attribute, fail_after_write)
    with pytest.raises(RuntimeError, match="injected late fault"):
        create_station(
            actor_id=actor.id,
            workshop_id=workshop.id,
            submission_key="fault",
            data={"name": "Cell A", "capability_ids": [operation_type.id]},
        )
    workshop.refresh_from_db()
    operation_type.refresh_from_db()
    assert workshop.station_code_counter == 0
    assert operation_type.first_referenced_at is None
    assert not Station.objects.filter(workshop=workshop).exists()
    assert not StationSupportedOperationType.objects.exists()
    assert not ConfigurationCommandReceipt.objects.filter(
        command_type="station_create"
    ).exists()
    assert not Event.objects.filter(event_type="STATION_CREATED").exists()
    assert not EventSubject.objects.exists()
    assert not EventNotificationIntent.objects.exists()


def test_operation_blocker_rejects_capability_removal_with_zero_writes(monkeypatch):
    actor, workshop = library_admin("station-edit-operation-blocker")
    operation_type = _type(workshop)
    created = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data={"name": "Cell A", "capability_ids": [operation_type.id]},
    )
    before_events = Event.objects.count()
    monkeypatch.setattr(
        station_dependencies,
        "locked_operations_blocking_capability_removal",
        lambda **kwargs: (StationBlocker("assigned_operation"),),
    )
    result = edit_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        station_code=created.station_code,
        expected_version=1,
        data={"name": "Changed", "capability_ids": []},
    )
    station = Station.objects.get(pk=created.station_id)
    assert result.code == "blocked"
    assert (station.name, station.version) == ("Cell A", 1)
    assert station.supported_operation_types.filter(pk=operation_type.id).exists()
    assert Event.objects.count() == before_events


@pytest.mark.parametrize(
    "adapter_name",
    [
        "locked_operations_blocking_retirement",
        "locked_maintenance_jobs_blocking_retirement",
    ],
)
def test_dependency_blockers_reject_retirement_with_zero_writes(
    monkeypatch, adapter_name
):
    actor, workshop = library_admin(f"station-retire-{adapter_name}")
    created = create_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data={"name": "Cell A", "capability_ids": []},
    )
    before_events = Event.objects.count()
    monkeypatch.setattr(
        station_dependencies,
        adapter_name,
        lambda **kwargs: (StationBlocker("non_terminal_dependency"),),
    )
    result = retire_station(
        actor_id=actor.id,
        workshop_id=workshop.id,
        station_code=created.station_code,
        expected_version=1,
    )
    station = Station.objects.get(pk=created.station_id)
    assert result.code == "blocked"
    assert (
        station.lifecycle_status,
        station.availability_status,
        station.version,
    ) == (Station.LifecycleStatus.ACTIVE, Station.AvailabilityStatus.AVAILABLE, 1)
    assert Event.objects.count() == before_events


@pytest.mark.parametrize(
    "invalid_shape",
    ["wrong_primary", "missing_primary", "wrong_role", "foreign_workshop"],
)
def test_station_event_subject_validation_rejects_wrong_missing_and_cross_tenant(
    invalid_shape,
):
    actor, workshop = library_admin(f"station-event-invalid-{invalid_shape}")
    _, foreign = library_admin(f"station-event-invalid-{invalid_shape}-foreign")
    station = Station.objects.create(workshop=workshop, code="ST-001", name="Cell")
    primary_type = "workshop" if invalid_shape == "wrong_primary" else "station"
    primary_id = 999_999 if invalid_shape == "missing_primary" else station.id
    role = "observer" if invalid_shape == "wrong_role" else "workshop"
    related_workshop = foreign if invalid_shape == "foreign_workshop" else workshop
    spec = EventSpec(
        event_type="STATION_UPDATED",
        occurred_at=timezone.now(),
        actor_type="user",
        actor_user_id=actor.id,
        primary_subject_type=primary_type,
        primary_subject_id=primary_id,
        payload={"version": 2},
        idempotency_key=f"invalid-{invalid_shape}",
        subjects=(EventSubjectSpec("workshop", related_workshop.id, role),),
    )
    before = (Event.objects.count(), EventSubject.objects.count())
    with transaction.atomic(), pytest.raises(ValueError):
        produce_events([spec])
    assert (Event.objects.count(), EventSubject.objects.count()) == before


def test_system_capability_loss_routes_to_permanent_admin_and_manager():
    admin, manager, _ = _operational_people("station-routing-system")
    operation_type = _type(admin.workshop)
    event = Event.objects.create(
        event_type="OPERATION_TYPE_CAPABILITY_LOST",
        occurred_at=timezone.now(),
        actor_type="system",
        primary_subject_type="operation_type",
        primary_subject_id=operation_type.id,
        payload={"cause": "no_station_support"},
        idempotency_key="station-routing-system",
    )
    assert {row.user_id for row in resolve_recipients(event)} == {
        admin.id,
        manager.id,
    }
