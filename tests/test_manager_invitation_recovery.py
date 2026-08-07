from datetime import timedelta

import pytest
from django.utils import timezone

from events.models import Event, Notification
from identity import commands
from identity.commands import (
    replace_pending_permanent_manager,
    resend_permanent_manager_invitation,
)
from identity.models import (
    EmailDeliveryIntent,
    ManagerInvitationCommandReceipt,
    User,
    UserInvitation,
)
from identity.queries import get_pending_manager_setup, get_public_invitation_envelope
from identity.results import ResultCode
from tests.test_manager_invitation import attached_admin, payload

pytestmark = pytest.mark.django_db(transaction=True)


def pending_setup(monkeypatch):
    deliveries = []
    monkeypatch.setattr(
        commands,
        "schedule_invitation_delivery",
        lambda **kwargs: deliveries.append(kwargs),
    )
    admin, workshop, _ = attached_admin()
    initial = commands.invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="initial"
    )
    workshop.refresh_from_db()
    return admin, workshop, initial.candidate, initial.invitation, deliveries


def resend_data(workshop, **overrides):
    data = {
        "invitation_action": "resend",
        "submission_nonce": "resend-browser-nonce",
        "expected_workshop_version": workshop.version,
    }
    data.update(overrides)
    return data


def replacement_data(workshop, **overrides):
    data = {
        "invitation_action": "replace",
        "submission_nonce": "replacement-browser-nonce",
        "expected_workshop_version": workshop.version,
        "first_name": "Riley",
        "last_name": "Replacement",
        "date_of_birth": "1992-06-19",
        "email": "riley@example.test",
    }
    data.update(overrides)
    return data


def test_cockpit_projection_is_current_derived_and_secret_free(monkeypatch):
    admin, workshop, candidate, invitation, _ = pending_setup(monkeypatch)
    created_at = timezone.now() - timedelta(hours=74)
    invitation.issued_at = created_at
    invitation.expires_at = timezone.now() - timedelta(hours=1)
    UserInvitation.objects.filter(pk=invitation.pk).update(
        created_at=created_at,
        issued_at=invitation.issued_at,
        expires_at=invitation.expires_at,
    )
    setup = get_pending_manager_setup(admin)
    assert setup == {
        "workshop_name": workshop.name,
        "workshop_timezone": workshop.timezone,
        "workshop_version": workshop.version,
        "candidate_name": f"{candidate.first_name} {candidate.last_name}",
        "candidate_email": candidate.email,
        "issued_at": invitation.issued_at,
        "expires_at": invitation.expires_at,
        "expired": True,
        "delivery_status": "pending",
        "can_resend": True,
        "can_replace": True,
    }
    assert not ({"token_hash", "token_salt", "invitation_generation"} & setup.keys())


def test_cockpit_replacement_hint_uses_command_zero_history_guard(monkeypatch):
    admin, workshop, candidate, invitation, _ = pending_setup(monkeypatch)
    Event.objects.create(
        event_type="SYNTHETIC_HISTORY",
        occurred_at=timezone.now(),
        actor_type="user",
        actor_user=candidate,
        primary_subject_type="user",
        primary_subject_id=candidate.id,
    )
    assert get_pending_manager_setup(admin)["can_replace"] is False
    result = replace_pending_permanent_manager(
        actor_id=admin.id,
        data=replacement_data(workshop),
        idempotency_key="history-hint",
    )
    assert result.code == ResultCode.INVITATION_UNAVAILABLE
    assert User.objects.filter(pk=candidate.id).exists()
    assert UserInvitation.objects.filter(pk=invitation.id).exists()


def test_cockpit_fails_closed_for_superseded_current_intent(monkeypatch):
    admin, _, _, invitation, _ = pending_setup(monkeypatch)
    EmailDeliveryIntent.objects.filter(
        invitation=invitation,
        invitation_generation=invitation.invitation_generation,
    ).update(status=EmailDeliveryIntent.Status.SUPERSEDED)
    assert get_pending_manager_setup(admin) is None


def test_resend_rotates_generation_and_invalidates_old_link(monkeypatch):
    admin, workshop, candidate, invitation, deliveries = pending_setup(monkeypatch)
    old = deliveries.pop()
    old_created = invitation.created_at
    old_hash = bytes(invitation.token_hash)
    old_salt = bytes(invitation.token_salt)
    result = resend_permanent_manager_invitation(
        actor_id=admin.id,
        data=resend_data(workshop),
        idempotency_key="resend-1",
    )
    assert result.code == ResultCode.SUCCESS and len(deliveries) == 1
    workshop.refresh_from_db()
    invitation.refresh_from_db()
    assert result.candidate.id == candidate.id and result.invitation.id == invitation.id
    assert invitation.created_at == old_created
    assert invitation.invitation_generation == 2
    assert bytes(invitation.token_hash) != old_hash
    assert bytes(invitation.token_salt) != old_salt
    assert invitation.expires_at - invitation.issued_at == timedelta(hours=72)
    assert workshop.version == 3
    assert EmailDeliveryIntent.objects.filter(invitation=invitation).count() == 2
    assert not get_public_invitation_envelope(
        str(invitation.id), old["raw_token"]
    ).available
    fresh = deliveries[0]
    assert fresh["generation"] == 2
    assert get_public_invitation_envelope(
        str(invitation.id), fresh["raw_token"]
    ).available
    assert Event.objects.count() == Notification.objects.count() == 0


@pytest.mark.parametrize("fault", ("supersession", "invitation", "intent", "workshop"))
def test_resend_faults_roll_back_complete_source(monkeypatch, fault):
    admin, workshop, _, invitation, _ = pending_setup(monkeypatch)
    before_invitation = tuple(UserInvitation.objects.values())
    before_intents = tuple(EmailDeliveryIntent.objects.values())
    with pytest.raises(RuntimeError):
        resend_permanent_manager_invitation(
            actor_id=admin.id,
            data=resend_data(workshop),
            idempotency_key=f"fault-{fault}",
            fault_after=fault,
        )
    workshop.refresh_from_db()
    assert tuple(UserInvitation.objects.values()) == before_invitation
    assert tuple(EmailDeliveryIntent.objects.values()) == before_intents
    assert workshop.version == 2 and invitation.pk


def test_replacement_atomically_removes_old_aggregate_and_replays(monkeypatch):
    admin, workshop, old_candidate, old_invitation, deliveries = pending_setup(
        monkeypatch
    )
    old_intent_ids = list(
        EmailDeliveryIntent.objects.filter(invitation=old_invitation).values_list(
            "id", flat=True
        )
    )
    result = replace_pending_permanent_manager(
        actor_id=admin.id,
        data=replacement_data(workshop),
        idempotency_key="replace-1",
    )
    assert result.code == ResultCode.SUCCESS and len(deliveries) == 2
    workshop.refresh_from_db()
    assert workshop.version == 3 and workshop.status == "manager_activation_pending"
    assert not User.objects.filter(pk=old_candidate.id).exists()
    assert not UserInvitation.objects.filter(pk=old_invitation.id).exists()
    assert not EmailDeliveryIntent.objects.filter(pk__in=old_intent_ids).exists()
    assert User.objects.filter(account_role="manager").count() == 1
    assert result.candidate.email == "riley@example.test"
    assert (
        result.candidate.status == "pending"
        and not result.candidate.has_usable_password()
    )
    assert result.invitation.invitation_generation == 1
    assert ManagerInvitationCommandReceipt.objects.count() == 1
    replay = replace_pending_permanent_manager(
        actor_id=admin.id,
        data=replacement_data(workshop, expected_workshop_version=2),
        idempotency_key="replace-1",
    )
    misuse = replace_pending_permanent_manager(
        actor_id=admin.id,
        data=replacement_data(
            workshop, expected_workshop_version=2, first_name="Changed"
        ),
        idempotency_key="replace-1",
    )
    assert replay.code == ResultCode.REPLAY
    assert misuse.code == ResultCode.INVITATION_UNAVAILABLE
    assert len(deliveries) == 2
    assert Event.objects.count() == Notification.objects.count() == 0


def test_replacement_rejects_history_duplicate_stale_and_invalid(monkeypatch):
    admin, workshop, candidate, invitation, _ = pending_setup(monkeypatch)
    Event.objects.create(
        event_type="SYNTHETIC_HISTORY",
        occurred_at=timezone.now(),
        actor_type="user",
        actor_user=candidate,
        primary_subject_type="user",
        primary_subject_id=candidate.id,
    )
    before = tuple(User.objects.values())
    history = replace_pending_permanent_manager(
        actor_id=admin.id,
        data=replacement_data(workshop),
        idempotency_key="history",
    )
    stale = replace_pending_permanent_manager(
        actor_id=admin.id,
        data=replacement_data(workshop, expected_workshop_version=99),
        idempotency_key="stale",
    )
    invalid = replace_pending_permanent_manager(
        actor_id=admin.id,
        data=replacement_data(workshop, email="bad"),
        idempotency_key="invalid",
    )
    assert history.code == ResultCode.INVITATION_UNAVAILABLE
    assert stale.code == ResultCode.STALE
    assert invalid.code == ResultCode.VALIDATION_ERROR
    assert tuple(User.objects.values()) == before
    assert UserInvitation.objects.get().id == invitation.id


@pytest.mark.parametrize(
    "fault", ("deletion", "user", "invitation", "intent", "receipt", "workshop")
)
def test_replacement_faults_restore_old_aggregate(monkeypatch, fault):
    admin, workshop, candidate, invitation, deliveries = pending_setup(monkeypatch)
    before_users = tuple(User.objects.values())
    before_invitation = tuple(UserInvitation.objects.values())
    before_intents = tuple(EmailDeliveryIntent.objects.values())
    before_receipts = tuple(ManagerInvitationCommandReceipt.objects.values())
    with pytest.raises(RuntimeError):
        replace_pending_permanent_manager(
            actor_id=admin.id,
            data=replacement_data(workshop),
            idempotency_key=f"replace-fault-{fault}",
            fault_after=fault,
        )
    assert tuple(User.objects.values()) == before_users
    assert tuple(UserInvitation.objects.values()) == before_invitation
    assert tuple(EmailDeliveryIntent.objects.values()) == before_intents
    assert tuple(ManagerInvitationCommandReceipt.objects.values()) == before_receipts
    assert len(deliveries) == 1 and candidate.pk and invitation.pk
