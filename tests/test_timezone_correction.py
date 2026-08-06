import pytest

from events.models import Event, EventNotificationIntent
from identity.commands import correct_workshop_timezone
from identity.models import User
from identity.results import ResultCode
from workshops.models import OperationType, Workshop, WorkshopRole


def make_admin_workshop(*, status=Workshop.Status.MANAGER_REQUIRED):
    WorkshopRole.objects.get_or_create(
        machine_key="undefined",
        defaults={"name": "undefined", "status": "active", "version": 1},
    )
    WorkshopRole.objects.get_or_create(
        machine_key="admin",
        defaults={"name": "Admin", "status": "active", "version": 1},
    )
    OperationType.objects.get_or_create(
        machine_key="other",
        defaults={
            "name": "Other",
            "is_production": True,
            "requires_clearance": False,
            "status": "active",
            "version": 1,
        },
    )
    workshop = Workshop.objects.create(
        name="Timezone QA",
        address="1 Test Street",
        email="qa@example.test",
        timezone="Europe/London",
        status=status,
    )
    admin_role = WorkshopRole.objects.get(machine_key="admin")
    user = User.objects.create_user(
        email=f"admin-{workshop.id}@example.test",
        password="Testing-passphrase-123!",
        first_name="Ada",
        last_name="Admin",
        date_of_birth="1990-04-17",
        account_role=User.AccountRole.ADMIN,
        status=User.Status.ACTIVE,
        workshop=workshop,
        workshop_role=admin_role,
        onboarding_state=None,
    )
    return user, workshop


def correction_data(workshop, *, zone="Europe/Paris"):
    return {
        "timezone_action": "correct",
        "submission_nonce": "nonce",
        "expected_workshop_version": workshop.version,
        "timezone": zone,
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [Workshop.Status.MANAGER_REQUIRED, Workshop.Status.MANAGER_ACTIVATION_PENDING],
)
def test_timezone_correction_is_atomic_and_eventful(status):
    user, workshop = make_admin_workshop(status=status)
    result = correct_workshop_timezone(
        actor_id=user.id,
        data=correction_data(workshop),
        idempotency_key="timezone-key",
    )
    workshop.refresh_from_db()
    assert result.code == ResultCode.SUCCESS
    assert (workshop.timezone, workshop.version) == ("Europe/Paris", 2)
    event = Event.objects.get(event_type="WORKSHOP_TIMEZONE_CHANGED")
    assert event.payload == {
        "old_timezone": "Europe/London",
        "new_timezone": "Europe/Paris",
    }
    assert event.actor_user_id == user.id
    assert EventNotificationIntent.objects.get(event=event).status == "pending"


@pytest.mark.django_db
def test_timezone_same_key_replays_and_different_key_closes():
    user, workshop = make_admin_workshop()
    data = correction_data(workshop)
    first = correct_workshop_timezone(
        actor_id=user.id, data=data, idempotency_key="timezone-key"
    )
    replay = correct_workshop_timezone(
        actor_id=user.id, data=data, idempotency_key="timezone-key"
    )
    closed = correct_workshop_timezone(
        actor_id=user.id, data=data, idempotency_key="different-key"
    )
    assert first.code == ResultCode.SUCCESS
    assert replay.code == ResultCode.REPLAY
    assert closed.code == ResultCode.ALREADY_ADVANCED
    assert Event.objects.count() == EventNotificationIntent.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("fault_after", ["workshop", "event", "intent"])
def test_timezone_fault_injection_rolls_back_everything(fault_after):
    user, workshop = make_admin_workshop()
    with pytest.raises(RuntimeError):
        correct_workshop_timezone(
            actor_id=user.id,
            data=correction_data(workshop),
            idempotency_key="timezone-key",
            fault_after=fault_after,
        )
    workshop.refresh_from_db()
    assert (workshop.timezone, workshop.version) == ("Europe/London", 1)
    assert workshop.timezone_correction_idempotency_key is None
    assert Event.objects.count() == EventNotificationIntent.objects.count() == 0


@pytest.mark.django_db
def test_timezone_stale_invalid_and_operational_attempts_are_silent():
    user, workshop = make_admin_workshop()
    stale = correction_data(workshop)
    stale["expected_workshop_version"] = 99
    assert (
        correct_workshop_timezone(
            actor_id=user.id, data=stale, idempotency_key="stale"
        ).code
        == ResultCode.STALE
    )
    invalid = correction_data(workshop, zone="Not/AZone")
    assert (
        correct_workshop_timezone(
            actor_id=user.id, data=invalid, idempotency_key="invalid"
        ).code
        == ResultCode.VALIDATION_ERROR
    )
    operational_user, operational_workshop = make_admin_workshop(
        status=Workshop.Status.OPERATIONAL
    )
    assert (
        correct_workshop_timezone(
            actor_id=operational_user.id,
            data=correction_data(operational_workshop),
            idempotency_key="closed",
        ).code
        == ResultCode.ALREADY_ADVANCED
    )
    assert Event.objects.count() == 0
