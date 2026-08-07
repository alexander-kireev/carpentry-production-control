import pytest
from django.test import override_settings

from events.models import Event, EventNotificationIntent, Notification
from events.processing import process_event_notification_intents
from identity import commands
from identity.commands import (
    accept_permanent_manager_invitation,
    create_workshop,
    invite_permanent_manager,
    register_administrator,
    replace_pending_permanent_manager,
    resend_permanent_manager_invitation,
)
from identity.models import EmailDeliveryIntent, User, UserInvitation
from identity.queries import (
    get_pending_manager_setup,
    get_public_invitation_envelope,
    resolve_authenticated_destination,
)
from identity.results import Destination, ResultCode
from tests.test_invitation_acceptance import PASSWORD
from tests.test_workshop_creation import ensure_protected_configuration

pytestmark = pytest.mark.django_db(transaction=True)


def registration_data(email):
    return {
        "submission_nonce": f"register-{email}",
        "first_name": "Ada",
        "last_name": "Admin",
        "date_of_birth": "1990-04-17",
        "email": email,
        "password": "Admin-valid-483!",
        "password_confirmation": "Admin-valid-483!",
        "activation_code": "slice-b-code",
    }


def create_data(user, suffix):
    return {
        "submission_nonce": f"workshop-{suffix}",
        "expected_user_version": user.version,
        "name": f"Slice B Workshop {suffix}",
        "address": f"{suffix} Joinery Lane",
        "contact_email": f"workshop-{suffix}@example.test",
        "timezone": "Europe/London",
    }


def manager_data(workshop, *, suffix="manager", first_name="Morgan"):
    return {
        "submission_nonce": f"manager-{suffix}",
        "expected_workshop_version": workshop.version,
        "first_name": first_name,
        "last_name": "Manager",
        "date_of_birth": "1991-05-18",
        "email": f"{suffix}@example.test",
    }


@override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="slice-b-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="slice-b-limiter-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=1,
)
def test_clean_slice_b_failed_delivery_recovery_activation_and_replacement(
    monkeypatch, settings
):
    ensure_protected_configuration()
    captured = []
    original_schedule = commands.schedule_invitation_delivery

    def capture_and_send(**kwargs):
        captured.append(kwargs)
        return original_schedule(**kwargs)

    monkeypatch.setattr(commands, "schedule_invitation_delivery", capture_and_send)

    registered = register_administrator(
        data=registration_data("ada-slice-b@example.test"),
        remote_addr="192.0.2.70",
        idempotency_key="slice-b-registration",
    )
    assert registered.code == ResultCode.SUCCESS
    created = create_workshop(
        actor_id=registered.user.id,
        data=create_data(registered.user, "one"),
        idempotency_key="slice-b-workshop",
    )
    assert created.code == ResultCode.SUCCESS
    settings.INVITATION_DELIVERY_MODE = "failing"
    invited = invite_permanent_manager(
        actor_id=registered.user.id,
        data=manager_data(created.workshop),
        idempotency_key="slice-b-invitation",
    )
    assert invited.code == ResultCode.SUCCESS
    old_token = captured[-1]["raw_token"]
    assert get_pending_manager_setup(registered.user)["delivery_status"] == "failed"

    created.workshop.refresh_from_db()
    settings.INVITATION_DELIVERY_MODE = "memory"
    resent = resend_permanent_manager_invitation(
        actor_id=registered.user.id,
        data={
            "invitation_action": "resend",
            "submission_nonce": "slice-b-resend",
            "expected_workshop_version": created.workshop.version,
        },
        idempotency_key="slice-b-resend",
    )
    assert resent.code == ResultCode.SUCCESS
    fresh_token = captured[-1]["raw_token"]
    assert not get_public_invitation_envelope(
        str(resent.invitation.id), old_token
    ).available
    assert get_public_invitation_envelope(
        str(resent.invitation.id), fresh_token
    ).available
    accepted = accept_permanent_manager_invitation(
        selector=str(resent.invitation.id),
        raw_token=fresh_token,
        password=PASSWORD,
        expected_generation=2,
    )
    assert accepted.code == ResultCode.SUCCESS
    assert Event.objects.count() == EventNotificationIntent.objects.count() == 2
    processing = process_event_notification_intents(limit=10)
    assert (processing.claimed, processing.processed, processing.failed) == (2, 2, 0)
    assert Notification.objects.count() == 1
    assert Notification.objects.get().recipient_user_id == registered.user.id
    assert (
        resolve_authenticated_destination(registered.user).destination
        == Destination.DASHBOARD
    )
    assert (
        resolve_authenticated_destination(accepted.user).destination
        == Destination.DASHBOARD
    )

    second = register_administrator(
        data=registration_data("ada-second@example.test"),
        remote_addr="192.0.2.71",
        idempotency_key="second-registration",
    )
    second_created = create_workshop(
        actor_id=second.user.id,
        data=create_data(second.user, "two"),
        idempotency_key="second-workshop",
    )
    initial = invite_permanent_manager(
        actor_id=second.user.id,
        data=manager_data(second_created.workshop, suffix="old-manager"),
        idempotency_key="second-invitation",
    )
    old_candidate_id = initial.candidate.id
    second_created.workshop.refresh_from_db()
    replacement = replace_pending_permanent_manager(
        actor_id=second.user.id,
        data=manager_data(
            second_created.workshop, suffix="replacement", first_name="Riley"
        )
        | {"invitation_action": "replace"},
        idempotency_key="second-replacement",
    )
    assert replacement.code == ResultCode.SUCCESS
    assert not User.objects.filter(pk=old_candidate_id).exists()
    assert UserInvitation.objects.filter(workshop=second_created.workshop).count() == 1
    assert (
        EmailDeliveryIntent.objects.filter(invitation=replacement.invitation).count()
        == 1
    )
    replacement_token = captured[-1]["raw_token"]
    replacement_acceptance = accept_permanent_manager_invitation(
        selector=str(replacement.invitation.id),
        raw_token=replacement_token,
        password=PASSWORD,
        expected_generation=1,
    )
    assert replacement_acceptance.code == ResultCode.SUCCESS
    assert (
        resolve_authenticated_destination(replacement_acceptance.user).destination
        == Destination.DASHBOARD
    )
