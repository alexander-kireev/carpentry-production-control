from datetime import date

import pytest
from django.contrib.auth import authenticate

from identity.models import User

pytestmark = pytest.mark.django_db


def make_user(**overrides):
    values = dict(
        email="member@example.test",
        password="Valid-password-483!",
        first_name="Test",
        last_name="Member",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )
    values.update(overrides)
    return User.objects.create_user(**values)


def test_backend_is_email_only_casefolded_and_active_only():
    user = make_user()
    assert (
        authenticate(email="MEMBER@EXAMPLE.TEST", password="Valid-password-483!")
        == user
    )
    assert (
        authenticate(username="member@example.test", password="Valid-password-483!")
        is None
    )
    assert (
        authenticate(email="missing@example.test", password="Valid-password-483!")
        is None
    )
    assert authenticate(email=user.email, password="wrong") is None


def test_inactive_and_unusable_credentials_reject():
    user = make_user()
    user.set_unusable_password()
    user.save(update_fields=("password",))
    assert authenticate(email=user.email, password="Valid-password-483!") is None


def test_login_rotates_session_and_logout_is_post_only(client):
    make_user()
    session = client.session
    session["preexisting"] = True
    session.save()
    old_key = session.session_key
    response = client.post(
        "/login", {"email": "member@example.test", "password": "Valid-password-483!"}
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/workshop"
    assert client.session.session_key != old_key
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").headers["Location"] == "/login"
    assert client.post("/logout").headers["Location"] == "/login"


def test_logout_rejects_missing_csrf():
    from django.test import Client

    user = make_user(email="csrf@example.test")
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(user)
    assert csrf_client.post("/logout").status_code == 403


def test_login_resume_uses_current_attached_state(client):
    from workshops.models import Workshop, WorkshopRole

    workshop = Workshop.objects.create(
        name="Resume",
        address="Address",
        email="resume-workshop@example.test",
        timezone="UTC",
    )
    user = make_user(
        email="resume@example.test",
        onboarding_state=None,
        workshop=workshop,
        workshop_role=WorkshopRole.objects.get(machine_key="admin"),
    )
    response = client.post(
        "/login", {"email": user.email, "password": "Valid-password-483!"}
    )
    assert response.headers["Location"] == "/onboarding/manager"


@pytest.mark.django_db(transaction=True)
def test_login_and_lost_response_resume_pending_cockpit(client):
    from identity.commands import invite_permanent_manager
    from tests.test_manager_invitation import attached_admin, payload

    user, _, _ = attached_admin(email="pending-resume@example.test")
    invite_permanent_manager(
        actor_id=user.id, data=payload(), idempotency_key="lost-response"
    )
    client.force_login(user)
    assert client.get("/onboarding/manager").status_code == 200
    assert client.post("/logout").headers["Location"] == "/login"
    response = client.post(
        "/login",
        {"email": user.email, "password": "Valid-password-483!"},
    )
    assert response.headers["Location"] == "/onboarding/manager"
    page = client.get("/onboarding/manager")
    assert b"Manager activation is pending" in page.content


def test_activated_manager_login_resumes_operational_handoff(client):
    from tests.test_invitation_acceptance import acceptance_fixture

    _, manager, workshop, invitation, token = acceptance_fixture()
    from identity.commands import accept_permanent_manager_invitation

    accept_permanent_manager_invitation(
        selector=str(invitation.id),
        raw_token=token,
        password="Manager-valid-483!",
        expected_generation=1,
    )
    response = client.post(
        "/login", {"email": manager.email, "password": "Manager-valid-483!"}
    )
    assert response.headers["Location"] == "/dashboard"
    assert workshop.users.filter(account_role="manager", status="active").count() == 1
