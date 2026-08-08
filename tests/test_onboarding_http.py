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
from identity.results import CommandResult, ResultCode
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
        b"Create your Workshop" in page.content and b"data-submit-once" in page.content
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
    assert client.get("/onboarding/workshop").status_code == 200


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
    assert b"Invite the permanent manager" in page.content
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
    assert posted.headers["Location"] == "/onboarding/manager"
    pending = client.get("/onboarding/manager")
    html = pending.content.lower()
    assert b"manager activation is pending" in html
    assert b"morgan manager" in html and b"manager-http@example.test" in html
    assert b"delivery" in html and b"awaiting activation" in html
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
    assert client.get("/onboarding/manager").status_code == 200


def test_manager_post_requires_csrf(client):
    user = admin()
    _create_workshop(client, user)
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert secure.post("/onboarding/manager", {}).status_code == 403


def test_timezone_control_corrects_once_without_hiding_manager_flow(client):
    user = admin()
    _create_workshop(client, user)
    page = client.get("/onboarding/workshop")
    timezone_form = page.context["timezone_form"]
    response = client.post(
        "/onboarding/workshop",
        {
            "timezone_action": "correct",
            "submission_nonce": timezone_form.initial["submission_nonce"],
            "expected_workshop_version": timezone_form.initial[
                "expected_workshop_version"
            ],
            "timezone": "Europe/Paris",
        },
    )
    assert response.headers["Location"] == "/onboarding/workshop"
    refreshed = client.get("/onboarding/workshop")
    assert b"Europe/Paris" in refreshed.content
    assert client.get("/onboarding/manager").status_code == 200


def test_timezone_stale_result_reopens_dialog_and_retains_safe_value(
    client, monkeypatch
):
    user = admin()
    _create_workshop(client, user)
    page = client.get("/onboarding/workshop")
    form = page.context["timezone_form"]
    monkeypatch.setattr(
        "identity.views.correct_workshop_timezone",
        lambda **kwargs: CommandResult(ResultCode.STALE, user=user),
    )
    response = client.post(
        "/onboarding/workshop",
        {
            "timezone_action": "correct",
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
            "timezone": "Europe/Paris",
        },
    )
    content = response.content.decode("utf-8")
    assert response.status_code == 400
    assert 'id="timezone-dialog"' in content and "data-dialog-auto-open" in content
    assert "Workshop setup changed" in content
    assert response.context["timezone_form"].data["timezone"] == "Europe/Paris"


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
    assert b"/invitations/" not in client.get("/onboarding/manager").content


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
    assert response.headers["Location"] == "/onboarding/manager"
    return user


def test_cockpit_recovery_controls_resend_and_do_not_duplicate_on_refresh(client):
    _reach_pending_cockpit(client)
    page = client.get("/onboarding/manager")
    html = page.content.lower()
    assert b"send a fresh invitation" in html
    assert b"replace pending manager" in html
    assert b"confirm resend" in html and b"replace and invite" in html
    rendered = page.content.decode()
    assert '<dialog class="library-dialog library-confirm-dialog"' in rendered
    assert not any(token in rendered for token in ("Ã", "Â", "â€"))
    resend = page.context["resend_form"]
    response = client.post(
        "/onboarding/manager",
        {
            "invitation_action": "resend",
            "submission_nonce": resend.initial["submission_nonce"],
            "expected_workshop_version": resend.initial["expected_workshop_version"],
        },
    )
    assert response.headers["Location"] == "/onboarding/manager"
    refreshed = client.get("/onboarding/manager")
    assert b"fresh invitation was committed" in refreshed.content.lower()
    assert UserInvitation.objects.get().invitation_generation == 2
    assert EmailDeliveryIntent.objects.count() == 2
    client.get("/onboarding/manager")
    assert UserInvitation.objects.get().invitation_generation == 2


def test_manager_stale_result_is_inside_reopened_affected_dialog(client, monkeypatch):
    _reach_pending_cockpit(client)
    page = client.get("/onboarding/manager")
    form = page.context["resend_form"]
    monkeypatch.setattr(
        "identity.views.resend_permanent_manager_invitation",
        lambda **kwargs: CommandResult(ResultCode.STALE),
    )
    response = client.post(
        "/onboarding/manager",
        {
            "invitation_action": "resend",
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
        },
    )
    content = response.content.decode("utf-8")
    dialog = content.split('id="resend-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert response.status_code == 400 and "data-dialog-auto-open" in dialog
    assert "Workshop setup changed" in dialog


def test_malformed_resend_announces_validation_inside_reopened_dialog(client):
    _reach_pending_cockpit(client)
    response = client.post(
        "/onboarding/manager",
        {"invitation_action": "resend"},
    )
    content = response.content.decode("utf-8")
    dialog = content.split('id="resend-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert response.status_code == 400 and "data-dialog-auto-open" in dialog
    assert 'role="alert"' in dialog
    assert "The resend request was invalid" in dialog


def test_cockpit_replacement_validation_and_success(client):
    _reach_pending_cockpit(client)
    old_candidate = User.objects.get(account_role="manager")
    page = client.get("/onboarding/manager")
    form = page.context["replacement_form"]
    invalid = client.post(
        "/onboarding/manager",
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
    page = client.get("/onboarding/manager")
    form = page.context["replacement_form"]
    replaced = client.post(
        "/onboarding/manager",
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
    assert replaced.headers["Location"] == "/onboarding/manager"
    refreshed = client.get("/onboarding/manager")
    assert b"riley replacement" in refreshed.content.lower()
    assert b"pending manager was replaced" in refreshed.content.lower()
    assert not User.objects.filter(pk=old_candidate.id).exists()


def test_cockpit_unknown_action_and_csrf_are_safe(client):
    user = _reach_pending_cockpit(client)
    before = (User.objects.count(), UserInvitation.objects.count())
    assert (
        client.post("/onboarding/manager", {"invitation_action": "unknown"}).status_code
        == 302
    )
    assert (User.objects.count(), UserInvitation.objects.count()) == before
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert (
        secure.post("/onboarding/manager", {"invitation_action": "resend"}).status_code
        == 403
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
    response = client.get("/onboarding/manager")
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
    response = client.get("/onboarding/manager", follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [("/login", 302)]
    assert "_auth_user_id" not in client.session


def test_pending_post_aggregate_loss_ends_session_without_redirect_loop(
    client, monkeypatch
):
    user = _reach_pending_cockpit(client)
    page = client.get("/onboarding/manager")
    form = page.context["resend_form"]

    def lose_aggregate(**kwargs):
        EmailDeliveryIntent.objects.all().delete()
        return CommandResult(ResultCode.ALREADY_ADVANCED, user=user)

    monkeypatch.setattr(
        "identity.views.resend_permanent_manager_invitation", lose_aggregate
    )
    response = client.post(
        "/onboarding/manager",
        {
            "invitation_action": "resend",
            "submission_nonce": form.initial["submission_nonce"],
            "expected_workshop_version": form.initial["expected_workshop_version"],
        },
        follow=True,
    )
    assert response.status_code == 200
    assert response.redirect_chain == [("/login", 302)]
    assert "_auth_user_id" not in client.session


def test_saved_workshop_and_manager_render_exact_safe_status_truth(client, monkeypatch):
    _reach_pending_cockpit(client)
    workshop = client.get("/onboarding/workshop").content.decode("utf-8")
    assert "Workshop status" in workshop
    assert "manager activation pending" in workshop.lower()
    assert "Identity editing" in workshop and "Unavailable until" in workshop

    real_projection = __import__(
        "identity.views", fromlist=["get_pending_manager_setup"]
    ).get_pending_manager_setup

    def sent_expired_projection(user):
        projection = real_projection(user)
        projection["delivery_status"] = "sent"
        projection["expired"] = True
        return projection

    monkeypatch.setattr(
        "identity.views.get_pending_manager_setup", sent_expired_projection
    )
    manager = client.get("/onboarding/manager").content.decode("utf-8")
    assert "Provider accepted" in manager
    assert "Inbox delivery is not confirmed" in manager
    assert "Expired" in manager
    assert all(
        secret not in manager.lower()
        for secret in ("token_hash", "generation", "receipt")
    )
