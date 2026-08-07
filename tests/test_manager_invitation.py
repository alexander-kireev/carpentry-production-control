from datetime import date

import pytest
from django.db import IntegrityError, transaction

from identity.commands import invite_permanent_manager
from identity.models import (
    EmailDeliveryIntent,
    ManagerInvitationCommandReceipt,
    User,
    UserInvitation,
)
from identity.results import ResultCode
from workshops.models import MaterialCategory, OperationType, Workshop, WorkshopRole

pytestmark = pytest.mark.django_db(transaction=True)


def attached_admin(*, email="admin@example.test"):
    undefined, _ = WorkshopRole.objects.get_or_create(
        machine_key="undefined", defaults={"name": "undefined", "status": "active"}
    )
    admin_role, _ = WorkshopRole.objects.get_or_create(
        machine_key="admin", defaults={"name": "Admin", "status": "active"}
    )
    OperationType.objects.get_or_create(
        machine_key="other",
        defaults={
            "name": "Other",
            "is_production": True,
            "requires_clearance": False,
            "status": "active",
        },
    )
    MaterialCategory.objects.get_or_create(
        machine_key="undefined",
        defaults={"name": "undefined", "status": "active", "version": 1},
    )
    workshop = Workshop.objects.create(
        name="Invitation Workshop",
        address="1 Joinery Lane",
        email="workshop@example.test",
        timezone="Europe/London",
    )
    for key, name in (
        ("build_planning", "Build Planning"),
        ("station_maintenance", "Station Maintenance"),
    ):
        OperationType.objects.create(
            workshop=workshop,
            machine_key=key,
            name=name,
            is_production=False,
            requires_clearance=True,
            status="active",
        )
    user = User.objects.create_user(
        email=email,
        password="Valid-password-483!",
        first_name="Ada",
        last_name="Admin",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state=None,
        workshop=workshop,
        workshop_role=admin_role,
        version=2,
    )
    return user, workshop, undefined


def payload(**overrides):
    values = {
        "submission_nonce": "manager-browser-nonce",
        "expected_workshop_version": 1,
        "first_name": "Morgan",
        "last_name": "Manager",
        "date_of_birth": "1991-05-18",
        "email": "MANAGER@example.test",
    }
    values.update(overrides)
    return values


def test_invite_commits_exact_aggregate_and_is_replay_safe():
    admin, workshop, undefined = attached_admin()
    result = invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="stable"
    )
    assert result.code == ResultCode.SUCCESS
    workshop.refresh_from_db()
    candidate = User.objects.get(account_role="manager")
    invitation = UserInvitation.objects.get()
    intent = EmailDeliveryIntent.objects.get()
    receipt = ManagerInvitationCommandReceipt.objects.get()
    assert workshop.status == "manager_activation_pending" and workshop.version == 2
    assert candidate.email == "manager@example.test"
    assert candidate.status == "pending" and not candidate.has_usable_password()
    assert candidate.workshop_role_id == undefined.id and candidate.version == 1
    assert invitation.user_id == candidate.id and invitation.workshop_id == workshop.id
    assert len(bytes(invitation.token_hash)) == 32
    assert bytes(invitation.token_salt) and invitation.invitation_generation == 1
    assert invitation.expires_at > invitation.issued_at
    assert intent.status == "sent" and intent.attempt_count == 1
    assert receipt.candidate_user_id == candidate.id
    replay = invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="stable"
    )
    misuse = invite_permanent_manager(
        actor_id=admin.id,
        data=payload(first_name="Changed"),
        idempotency_key="stable",
    )
    assert replay.code == ResultCode.REPLAY
    assert misuse.code == ResultCode.INVITATION_UNAVAILABLE
    assert User.objects.filter(account_role="manager").count() == 1
    assert EmailDeliveryIntent.objects.get().attempt_count == 1


def test_invalid_stale_self_and_duplicate_identity_write_nothing():
    admin, workshop, _ = attached_admin()
    duplicate = User.objects.create_user(
        email="used@example.test",
        password="Valid-password-483!",
        first_name="Used",
        last_name="Identity",
        date_of_birth=date(1990, 1, 1),
        account_role="operator",
        status="active",
        onboarding_state=None,
        workshop=workshop,
        workshop_role=WorkshopRole.objects.get(machine_key="undefined"),
    )
    cases = (
        payload(first_name=""),
        payload(expected_workshop_version=99),
        payload(email=admin.email),
        payload(email=duplicate.email),
    )
    results = [
        invite_permanent_manager(actor_id=admin.id, data=data, idempotency_key=str(i))
        for i, data in enumerate(cases)
    ]
    assert [result.code for result in results] == [
        ResultCode.VALIDATION_ERROR,
        ResultCode.STALE,
        ResultCode.INVITATION_UNAVAILABLE,
        ResultCode.INVITATION_UNAVAILABLE,
    ]
    assert UserInvitation.objects.count() == 0
    assert ManagerInvitationCommandReceipt.objects.count() == 0
    workshop.refresh_from_db()
    assert (workshop.status, workshop.version) == ("manager_required", 1)


@pytest.mark.parametrize(
    "target", ("candidate", "invitation", "intent", "receipt", "workshop")
)
def test_injected_material_failure_rolls_back_all(monkeypatch, target):
    admin, workshop, _ = attached_admin()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic")

    if target == "candidate":
        monkeypatch.setattr(User, "save", fail)
    elif target == "invitation":
        monkeypatch.setattr(UserInvitation.objects, "create", fail)
    elif target == "intent":
        monkeypatch.setattr(EmailDeliveryIntent.objects, "create", fail)
    elif target == "receipt":
        monkeypatch.setattr(ManagerInvitationCommandReceipt.objects, "create", fail)
    else:
        monkeypatch.setattr(Workshop, "save", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        invite_permanent_manager(
            actor_id=admin.id, data=payload(), idempotency_key="rollback"
        )
    workshop.refresh_from_db()
    assert (workshop.status, workshop.version) == ("manager_required", 1)
    assert User.objects.filter(account_role="manager").count() == 0
    assert UserInvitation.objects.count() == 0
    assert EmailDeliveryIntent.objects.count() == 0
    assert ManagerInvitationCommandReceipt.objects.count() == 0


def test_receipt_is_immutable_and_candidate_cascade_removes_aggregate():
    admin, _, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="cascade"
    )
    receipt = ManagerInvitationCommandReceipt.objects.get()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ManagerInvitationCommandReceipt.objects.filter(pk=receipt.pk).update(
                idempotency_key="changed"
            )
    candidate = User.objects.get(account_role="manager")
    candidate.delete()
    assert UserInvitation.objects.count() == 0
    assert EmailDeliveryIntent.objects.count() == 0
    assert ManagerInvitationCommandReceipt.objects.count() == 0
