from datetime import date

import pytest

from identity.commands import create_workshop
from identity.models import User, WorkshopCreationCommandReceipt
from identity.results import ResultCode
from workshops.models import OperationType, Workshop, WorkshopRole

pytestmark = pytest.mark.django_db(transaction=True)


def admin():
    ensure_protected_configuration()
    return User.objects.create_user(
        email="creator@example.test",
        password="Valid-password-483!",
        first_name="Workshop",
        last_name="Creator",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )


def ensure_protected_configuration():
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


def payload(**overrides):
    values = {
        "submission_nonce": "browser-nonce",
        "expected_user_version": 1,
        "name": "Northfield Joinery",
        "address": "1 Workshop Lane",
        "contact_email": "WORKSHOP@example.test",
        "timezone": "Europe/London",
    }
    values.update(overrides)
    return values


def test_create_workshop_commits_exact_graph_and_is_silent():
    user = admin()
    result = create_workshop(actor_id=user.id, data=payload(), idempotency_key="key")
    assert result.code == ResultCode.SUCCESS
    user.refresh_from_db()
    workshop = Workshop.objects.get()
    assert (workshop.name, workshop.address, workshop.email, workshop.timezone) == (
        "Northfield Joinery",
        "1 Workshop Lane",
        "workshop@example.test",
        "Europe/London",
    )
    assert (workshop.status, workshop.version) == ("manager_required", 1)
    assert user.workshop_id == workshop.id
    assert user.workshop_role.machine_key == "admin"
    assert user.onboarding_state is None and user.version == 2
    assert set(
        OperationType.objects.filter(workshop=workshop).values_list(
            "machine_key", "name", "is_production", "requires_clearance", "version"
        )
    ) == {
        ("build_planning", "Build Planning", False, True, 1),
        ("station_maintenance", "Station Maintenance", False, True, 1),
    }
    receipt = WorkshopCreationCommandReceipt.objects.get()
    assert (receipt.actor_user_id, receipt.result_workshop_id) == (user.id, workshop.id)


def test_validation_stale_replay_and_misuse_are_safe():
    user = admin()
    invalid = create_workshop(
        actor_id=user.id,
        data=payload(timezone="Not/AZone"),
        idempotency_key="invalid",
    )
    stale = create_workshop(
        actor_id=user.id,
        data=payload(expected_user_version=2),
        idempotency_key="stale",
    )
    assert invalid.code == ResultCode.VALIDATION_ERROR
    assert stale.code == ResultCode.STALE
    assert Workshop.objects.count() == 0

    assert create_workshop(
        actor_id=user.id, data=payload(), idempotency_key="stable"
    ).succeeded
    replay = create_workshop(actor_id=user.id, data=payload(), idempotency_key="stable")
    misuse = create_workshop(
        actor_id=user.id, data=payload(name="Changed"), idempotency_key="stable"
    )
    assert replay.code == ResultCode.REPLAY
    assert misuse.code == ResultCode.WORKSHOP_UNAVAILABLE
    assert (
        Workshop.objects.count() == WorkshopCreationCommandReceipt.objects.count() == 1
    )


@pytest.mark.parametrize(
    "failure_point",
    ("after_workshop", "after_pair", "after_attachment", "after_receipt"),
)
def test_injected_failure_rolls_back_every_material_leg(monkeypatch, failure_point):
    user = admin()

    if failure_point == "after_workshop":
        monkeypatch.setattr(
            OperationType.objects,
            "bulk_create",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("synthetic after Workshop")
            ),
        )
    elif failure_point == "after_pair":
        monkeypatch.setattr(
            User,
            "save",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("synthetic after protected pair")
            ),
        )
    elif failure_point == "after_attachment":
        monkeypatch.setattr(
            WorkshopCreationCommandReceipt.objects,
            "create",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("synthetic after attachment")
            ),
        )
    else:
        original_create = WorkshopCreationCommandReceipt.objects.create

        def create_then_fail(*args, **kwargs):
            original_create(*args, **kwargs)
            raise RuntimeError("synthetic after receipt")

        monkeypatch.setattr(
            WorkshopCreationCommandReceipt.objects, "create", create_then_fail
        )

    with pytest.raises(RuntimeError, match="synthetic"):
        create_workshop(actor_id=user.id, data=payload(), idempotency_key="rollback")
    user.refresh_from_db()
    assert user.workshop_id is None and user.version == 1
    assert (
        Workshop.objects.count() == WorkshopCreationCommandReceipt.objects.count() == 0
    )


def test_corrupt_replay_fails_closed_without_repair():
    from django.db import connection

    user = admin()
    assert create_workshop(
        actor_id=user.id, data=payload(), idempotency_key="corrupt-replay"
    ).succeeded
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE operation_type DISABLE TRIGGER cst_046_operation_type_guard"
        )
        cursor.execute(
            "UPDATE operation_type SET name='Corrupt' "
            "WHERE workshop_id IS NOT NULL AND machine_key='build_planning'"
        )
    replay = create_workshop(
        actor_id=user.id, data=payload(), idempotency_key="corrupt-replay"
    )
    assert replay.code == ResultCode.WORKSHOP_UNAVAILABLE
    assert (
        OperationType.objects.get(
            workshop__isnull=False, machine_key="build_planning"
        ).name
        == "Corrupt"
    )


def test_missing_global_protected_configuration_fails_closed(monkeypatch):
    user = admin()
    from workshops.protected_configuration import ProtectedConfigurationError

    def fail_closed():
        raise ProtectedConfigurationError("synthetic missing protected identity")

    monkeypatch.setattr("identity.commands.resolve_admin_role", fail_closed)
    result = create_workshop(actor_id=user.id, data=payload(), idempotency_key="closed")
    assert result.code == ResultCode.WORKSHOP_UNAVAILABLE
    assert Workshop.objects.count() == 0
