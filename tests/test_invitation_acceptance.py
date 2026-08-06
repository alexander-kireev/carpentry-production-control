from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from events.models import Event, EventNotificationIntent, Notification
from events.processing import process_event_notification_intents
from identity.commands import accept_permanent_manager_invitation
from identity.models import User, UserInvitation
from identity.results import ResultCode
from identity.security import generate_invitation_token
from workshops.models import OperationType, Workshop, WorkshopRole

pytestmark = pytest.mark.django_db(transaction=True)
PASSWORD = "Manager-valid-483!"


def acceptance_fixture(*, expired=False):
    admin_role, _ = WorkshopRole.objects.get_or_create(
        machine_key="admin", defaults={"name": "Admin", "status": "active"}
    )
    undefined, _ = WorkshopRole.objects.get_or_create(
        machine_key="undefined", defaults={"name": "undefined", "status": "active"}
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
    workshop = Workshop.objects.create(
        name="Acceptance Workshop",
        address="1 Safe Lane",
        email="workshop@example.test",
        timezone="Europe/London",
        status="manager_activation_pending",
        version=2,
    )
    admin = User.objects.create_user(
        email="ada@example.test",
        password="Admin-valid-483!",
        first_name="Ada",
        last_name="Admin",
        date_of_birth=date(1990, 4, 17),
        account_role="admin",
        status="active",
        onboarding_state=None,
        workshop=workshop,
        workshop_role=admin_role,
        version=2,
    )
    manager = User.objects.create_user(
        email="morgan@example.test",
        password=None,
        first_name="Morgan",
        last_name="Manager",
        date_of_birth=date(1991, 5, 18),
        account_role="manager",
        status="pending",
        onboarding_state=None,
        workshop=workshop,
        workshop_role=undefined,
        version=1,
    )
    raw_token, salt, digest = generate_invitation_token()
    now = timezone.now()
    issued_at = now - (timedelta(hours=74) if expired else timedelta(hours=1))
    invitation = UserInvitation.objects.create(
        user=manager,
        workshop=workshop,
        token_hash=digest,
        token_hash_version=1,
        token_salt=salt,
        invitation_generation=1,
        status="pending",
        created_at=issued_at,
        issued_at=issued_at,
        expires_at=now - timedelta(hours=2) if expired else now + timedelta(hours=71),
    )
    return admin, manager, workshop, invitation, raw_token


def test_valid_get_is_write_free_and_minimal(client):
    _, manager, _, invitation, token = acceptance_fixture()
    before = (Event.objects.count(), EventNotificationIntent.objects.count())
    response = client.get(f"/invitations/{invitation.id}/{token}")
    assert response.status_code == 200
    assert (
        b"Morgan Manager" in response.content
        and manager.email.encode() in response.content
    )
    assert token.encode() not in response.content
    assert response.headers["Referrer-Policy"] == "origin"
    assert b'<meta name="referrer" content="origin">' in response.content
    assert response.headers["Cache-Control"] == "no-store, private"
    assert (Event.objects.count(), EventNotificationIntent.objects.count()) == before


@pytest.mark.parametrize("kind", ("malformed", "wrong", "expired", "consumed"))
def test_unavailable_causes_converge(client, kind):
    _, _, _, invitation, token = acceptance_fixture(expired=kind == "expired")
    selector = str(invitation.id)
    if kind == "malformed":
        selector = "not-a-number"
    elif kind == "wrong":
        token = "x" * 43
    elif kind == "consumed":
        invitation.status = "consumed"
        invitation.save(update_fields=("status",))
    response = client.get(f"/invitations/{selector}/{token}")
    assert response.status_code == 404
    assert b"This invitation is unavailable" in response.content
    assert b"Go to Login" in response.content
    assert token.encode() not in response.content


def test_acceptance_commits_exact_transition_events_and_processing():
    admin, manager, workshop, invitation, token = acceptance_fixture()
    result = accept_permanent_manager_invitation(
        selector=str(invitation.id),
        raw_token=token,
        password=PASSWORD,
        expected_generation=1,
    )
    assert result.code == ResultCode.SUCCESS
    manager.refresh_from_db()
    workshop.refresh_from_db()
    invitation.refresh_from_db()
    assert (
        manager.status == "active"
        and manager.version == 2
        and manager.check_password(PASSWORD)
    )
    assert invitation.status == "consumed"
    assert workshop.status == "operational" and workshop.version == 3
    events = list(Event.objects.order_by("id"))
    assert [event.event_type for event in events] == [
        "USER_INVITATION_ACCEPTED",
        "WORKSHOP_BECAME_OPERATIONAL",
    ]
    assert events[0].correlation_key == events[1].correlation_key
    assert events[0].idempotency_key != events[1].idempotency_key
    assert all(event.causation_event_id is None for event in events)
    assert EventNotificationIntent.objects.count() == 2
    counts = process_event_notification_intents()
    assert counts.processed == 2 and counts.notifications == 1
    assert Notification.objects.get().recipient_user_id == admin.id


@pytest.mark.parametrize(
    "fault", ("user", "invitation", "workshop", "first_event", "second_event")
)
def test_faults_roll_back_everything(fault):
    _, manager, workshop, invitation, token = acceptance_fixture()
    with pytest.raises(RuntimeError):
        accept_permanent_manager_invitation(
            selector=str(invitation.id),
            raw_token=token,
            password=PASSWORD,
            expected_generation=1,
            fault_after=fault,
        )
    manager.refresh_from_db()
    workshop.refresh_from_db()
    invitation.refresh_from_db()
    assert (manager.status, manager.version, manager.has_usable_password()) == (
        "pending",
        1,
        False,
    )
    assert (invitation.status, workshop.status, workshop.version) == (
        "pending",
        "manager_activation_pending",
        2,
    )
    assert Event.objects.count() == EventNotificationIntent.objects.count() == 0


def test_http_validation_then_success_rotates_session(client):
    _, manager, _, invitation, token = acceptance_fixture()
    path = f"/invitations/{invitation.id}/{token}"
    bad = client.post(path, {"password": "short", "password_confirmation": "different"})
    assert bad.status_code == 400 and b'value="short"' not in bad.content
    assert User.objects.get(pk=manager.id).status == "pending"
    session = client.session
    session["old"] = True
    session.save()
    old_key = session.session_key
    response = client.post(
        path, {"password": PASSWORD, "password_confirmation": PASSWORD}
    )
    assert response.status_code == 302 and response.headers["Location"] == "/dashboard"
    assert client.session.session_key != old_key
    assert client.get(path).status_code == 404
    assert client.get("/dashboard").status_code == 200


def test_csrf_accepts_exact_origin_but_rejects_null_and_untrusted_origins():
    _, manager, _, invitation, token = acceptance_fixture()
    path = f"/invitations/{invitation.id}/{token}"
    secure = Client(enforce_csrf_checks=True)
    assert secure.get(path).status_code == 200
    csrf = secure.cookies["csrftoken"].value
    data = {
        "csrfmiddlewaretoken": csrf,
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
    }
    assert secure.post(path, data, HTTP_ORIGIN="null").status_code == 403
    assert (
        secure.post(path, data, HTTP_ORIGIN="https://untrusted.example").status_code
        == 403
    )
    assert User.objects.get(pk=manager.id).status == "pending"
    accepted = secure.post(path, data, HTTP_ORIGIN="http://testserver")
    assert accepted.status_code == 302
    assert accepted.headers["Location"] == "/dashboard"
    assert token not in accepted.headers["Location"]


def test_post_commit_session_failure_keeps_activation_and_login_recovers(
    client, monkeypatch
):
    _, manager, workshop, invitation, token = acceptance_fixture()
    path = f"/invitations/{invitation.id}/{token}"
    monkeypatch.setattr(
        "identity.views.establish_session",
        lambda request, user: type("Failed", (), {"succeeded": False})(),
    )
    response = client.post(
        path, {"password": PASSWORD, "password_confirmation": PASSWORD}
    )
    assert response.headers["Location"] == "/login"
    manager.refresh_from_db()
    workshop.refresh_from_db()
    invitation.refresh_from_db()
    assert manager.status == "active" and invitation.status == "consumed"
    assert workshop.status == "operational" and Event.objects.count() == 2
    assert client.get(path).status_code == 404
    monkeypatch.undo()
    recovered = client.post("/login", {"email": manager.email, "password": PASSWORD})
    assert recovered.headers["Location"] == "/dashboard"
    assert Event.objects.count() == 2


def test_lock_order_is_workshop_then_users_then_invitation():
    _, _, _, invitation, token = acceptance_fixture()
    observed = []

    from django.db import connection

    def capture(execute, sql, params, many, context):
        if "FOR UPDATE" in sql:
            for table in ("workshop", "user_account", "user_invitation"):
                if f'FROM "{table}"' in sql:
                    observed.append(table)
                    break
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        result = accept_permanent_manager_invitation(
            selector=str(invitation.id),
            raw_token=token,
            password=PASSWORD,
            expected_generation=1,
        )
    assert result.code == ResultCode.SUCCESS
    assert observed[:3] == ["workshop", "user_account", "user_invitation"]
