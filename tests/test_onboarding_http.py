from datetime import date

import pytest
from django.test import Client
from django.utils import timezone

from events.models import Event
from identity.models import (
    EmailDeliveryIntent,
    ManagerInvitationCommandReceipt,
    User,
    UserInvitation,
    WorkshopCreationCommandReceipt,
)
from workshops.models import MaterialCategory, OperationType, WorkshopRole

pytestmark = pytest.mark.django_db(transaction=True)


def admin():
    WorkshopRole.objects.get_or_create(
        machine_key="undefined", defaults={"name": "undefined", "status": "active"}
    )
    WorkshopRole.objects.get_or_create(
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
    return User.objects.create_user(
        email="http-creator@example.test",
        password="Valid-password-483!",
        first_name="HTTP",
        last_name="Creator",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )


def test_create_workshop_page_and_post_success(client):
    user = admin()
    client.force_login(user)
    page = client.get("/onboarding/workshop")
    assert page.status_code == 200
    assert (
        b"Create your workshop" in page.content and b"data-submit-once" in page.content
    )
    form = page.context["form"]
    response = client.post(
        "/onboarding/workshop",
        {
            "submission_nonce": form.initial["submission_nonce"],
            "expected_user_version": form.initial["expected_user_version"],
            "name": "HTTP Workshop",
            "address": "1 HTTP Lane",
            "contact_email": "contact@example.test",
            "timezone": "Europe/London",
        },
    )
    assert response.headers["Location"] == "/onboarding/manager"
    handoff = client.get(response.headers["Location"])
    assert b"Workshop saved" in handoff.content
    assert b'id="id_first_name"' in handoff.content
    assert b"generation" not in handoff.content.lower()
    assert WorkshopCreationCommandReceipt.objects.count() == 1
    assert (
        client.get("/onboarding/workshop").headers["Location"] == "/onboarding/manager"
    )


def test_csrf_and_trailing_slashes_are_rejected(client):
    user = admin()
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert secure.post("/onboarding/workshop", {}).status_code == 403
    assert client.get("/onboarding/workshop/").status_code in {301, 302, 404}


def test_validation_retains_non_secret_fields(client):
    user = admin()
    client.force_login(user)
    page = client.get("/onboarding/workshop")
    response = client.post(
        "/onboarding/workshop",
        {
            "submission_nonce": page.context["form"].initial["submission_nonce"],
            "expected_user_version": 1,
            "name": "Kept",
            "address": "",
            "contact_email": "bad",
            "timezone": "Not/AZone",
        },
    )
    assert response.status_code == 400 and b"Kept" in response.content
    assert b"Select a valid choice" in response.content


def _create_workshop(client, user):
    client.force_login(user)
    page = client.get("/onboarding/workshop")
    form = page.context["form"]
    return client.post(
        "/onboarding/workshop",
        {
            "submission_nonce": form.initial["submission_nonce"],
            "expected_user_version": form.initial["expected_user_version"],
            "name": "HTTP Invitation Workshop",
            "address": "2 HTTP Lane",
            "contact_email": "invite-workshop@example.test",
            "timezone": "Europe/London",
        },
    )


def test_manager_form_invalid_post_and_committed_pending_cockpit(client):
    user = admin()
    response = _create_workshop(client, user)
    assert response.headers["Location"] == "/onboarding/manager"
    page = client.get("/onboarding/manager")
    assert page.status_code == 200
    assert b"Invite your permanent manager" in page.content
    assert b"data-submit-once" in page.content and b"Send invitation" in page.content
    form = page.context["form"]
    invalid = client.post(
        "/onboarding/manager",
        {
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
            "first_name": "Morgan",
            "last_name": "Manager",
            "date_of_birth": "",
            "email": "bad",
        },
    )
    assert invalid.status_code == 400 and b"Morgan" in invalid.content
    assert UserInvitation.objects.count() == 0
    page = client.get("/onboarding/manager")
    form = page.context["form"]
    posted = client.post(
        "/onboarding/manager",
        {
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
            "first_name": "Morgan",
            "last_name": "Manager",
            "date_of_birth": "1991-05-18",
            "email": "manager-http@example.test",
        },
    )
    assert posted.headers["Location"] == "/onboarding"
    pending = client.get("/onboarding")
    html = pending.content.lower()
    assert b"manager activation pending" in html
    assert b"morgan manager" in html and b"manager-http@example.test" in html
    assert b"provider accepted" in html and b"not yet confirmed" in html
    for forbidden in (
        b"/invitations/",
        b"token_hash",
        b"generation",
        b"receipt",
    ):
        assert forbidden not in html
    assert b"1991-05-18" not in html
    assert UserInvitation.objects.count() == 1
    assert EmailDeliveryIntent.objects.count() == 1
    assert ManagerInvitationCommandReceipt.objects.count() == 1
    assert client.get("/onboarding/manager").headers["Location"] == "/onboarding"


def test_manager_post_requires_csrf(client):
    user = admin()
    _create_workshop(client, user)
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert secure.post("/onboarding/manager", {}).status_code == 403


def test_timezone_control_corrects_once_without_hiding_manager_flow(client):
    user = admin()
    _create_workshop(client, user)
    page = client.get("/onboarding/manager")
    timezone_form = page.context["timezone_form"]
    response = client.post(
        "/onboarding/manager",
        {
            "timezone_action": "correct",
            "submission_nonce": timezone_form.initial["submission_nonce"],
            "expected_workshop_version": timezone_form.initial[
                "expected_workshop_version"
            ],
            "timezone": "Europe/Paris",
        },
    )
    assert response.headers["Location"] == "/onboarding/manager"
    refreshed = client.get("/onboarding/manager")
    assert b"Europe/Paris" in refreshed.content
    assert b"one-time timezone correction is closed" in refreshed.content.lower()
    assert b"Invite your permanent manager" in refreshed.content


def test_pending_cockpit_exposes_no_acceptance_credential_route(client):
    user = admin()
    _create_workshop(client, user)
    page = client.get("/onboarding/manager")
    form = page.context["form"]
    client.post(
        "/onboarding/manager",
        {
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
            "first_name": "Morgan",
            "last_name": "Manager",
            "date_of_birth": "1991-05-18",
            "email": "manager-private@example.test",
        },
    )
    assert b"/invitations/" not in client.get("/onboarding").content


def _reach_pending_cockpit(client):
    user = admin()
    _create_workshop(client, user)
    page = client.get("/onboarding/manager")
    form = page.context["form"]
    response = client.post(
        "/onboarding/manager",
        {
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
            "first_name": "Morgan",
            "last_name": "Manager",
            "date_of_birth": "1991-05-18",
            "email": "manager-recovery@example.test",
        },
    )
    assert response.headers["Location"] == "/onboarding"
    return user


def test_cockpit_recovery_controls_resend_and_do_not_duplicate_on_refresh(client):
    _reach_pending_cockpit(client)
    page = client.get("/onboarding")
    html = page.content.lower()
    assert b"send a fresh invitation" in html
    assert b"replace pending manager" in html
    assert b"confirm resend" in html and b"confirm replacement" in html
    rendered = page.content.decode()
    assert '<section class="onboarding-card timezone-correction"' in rendered
    assert "Saving timezone…" in rendered
    assert "Committing…" in rendered
    assert not any(token in rendered for token in ("Ã", "Â", "â€"))
    resend = page.context["resend_form"]
    response = client.post(
        "/onboarding",
        {
            "invitation_action": "resend",
            "submission_nonce": resend.initial["submission_nonce"],
            "expected_workshop_version": resend.initial["expected_workshop_version"],
        },
    )
    assert response.headers["Location"] == "/onboarding"
    refreshed = client.get("/onboarding")
    assert b"previous link is now unavailable" in refreshed.content.lower()
    assert UserInvitation.objects.get().invitation_generation == 2
    assert EmailDeliveryIntent.objects.count() == 2
    client.get("/onboarding")
    assert UserInvitation.objects.get().invitation_generation == 2


def test_cockpit_replacement_validation_and_success(client):
    _reach_pending_cockpit(client)
    old_candidate = User.objects.get(account_role="manager")
    page = client.get("/onboarding")
    form = page.context["replacement_form"]
    invalid = client.post(
        "/onboarding",
        {
            "invitation_action": "replace",
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
            "first_name": "Kept",
            "last_name": "",
            "date_of_birth": "1992-06-19",
            "email": "bad",
        },
    )
    assert invalid.status_code == 400 and b"Kept" in invalid.content
    assert User.objects.filter(pk=old_candidate.id).exists()
    page = client.get("/onboarding")
    form = page.context["replacement_form"]
    replaced = client.post(
        "/onboarding",
        {
            "invitation_action": "replace",
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
            "first_name": "Riley",
            "last_name": "Replacement",
            "date_of_birth": "1992-06-19",
            "email": "riley-http@example.test",
        },
    )
    assert replaced.headers["Location"] == "/onboarding"
    refreshed = client.get("/onboarding")
    assert b"riley replacement" in refreshed.content.lower()
    assert b"pending manager was replaced" in refreshed.content.lower()
    assert not User.objects.filter(pk=old_candidate.id).exists()


def test_cockpit_unknown_action_and_csrf_are_safe(client):
    user = _reach_pending_cockpit(client)
    before = (User.objects.count(), UserInvitation.objects.count())
    assert (
        client.post("/onboarding", {"invitation_action": "unknown"}).status_code == 302
    )
    assert (User.objects.count(), UserInvitation.objects.count()) == before
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert (
        secure.post("/onboarding", {"invitation_action": "resend"}).status_code == 403
    )


def test_cockpit_hides_replacement_when_candidate_has_history(client):
    _reach_pending_cockpit(client)
    candidate = User.objects.get(account_role="manager")
    invitation = UserInvitation.objects.get(user=candidate)
    Event.objects.create(
        event_type="SYNTHETIC_HISTORY",
        occurred_at=timezone.now(),
        actor_type="user",
        actor_user=candidate,
        primary_subject_type="user",
        primary_subject_id=candidate.id,
    )
    response = client.get("/onboarding")
    assert response.status_code == 200
    assert b"replace pending manager" not in response.content.lower()
    assert b"confirm replacement" not in response.content.lower()
    assert User.objects.filter(pk=candidate.id).exists()
    assert UserInvitation.objects.filter(pk=invitation.id).exists()


def test_cockpit_fails_closed_for_superseded_current_delivery(client, monkeypatch):
    monkeypatch.setattr(
        "identity.commands.schedule_invitation_delivery", lambda **kwargs: None
    )
    _reach_pending_cockpit(client)
    intent = EmailDeliveryIntent.objects.get()
    EmailDeliveryIntent.objects.filter(pk=intent.pk).update(status="superseded")
    response = client.get("/onboarding")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
