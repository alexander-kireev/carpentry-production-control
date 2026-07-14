"""Auth flow (F2): email login success/failure, logout, and the auth-required
landing. Exercises Django's LoginView/LogoutView wired against the email-based
custom user."""

import pytest

from tests.factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db


def test_login_with_valid_email_and_password_starts_session(client):
    user = UserFactory()

    response = client.post(
        "/login",
        {"username": user.email, "password": DEFAULT_PASSWORD},
    )

    assert response.status_code == 302
    assert response["Location"] == "/"
    assert client.session.get("_auth_user_id") == str(user.pk)


def test_login_with_invalid_password_shows_form_error_not_500(client):
    user = UserFactory()

    response = client.post(
        "/login",
        {"username": user.email, "password": "wrong-password"},
    )

    # Re-renders the form (200) with an error; no session established, no 500.
    assert response.status_code == 200
    assert response.context["form"].errors
    assert "_auth_user_id" not in client.session


def test_login_respects_next_parameter(client):
    user = UserFactory()

    response = client.post(
        "/login",
        {"username": user.email, "password": DEFAULT_PASSWORD, "next": "/"},
    )

    assert response.status_code == 302
    assert response["Location"] == "/"


def test_login_rejects_offsite_next_redirect(client):
    """Open-redirect guard: a hostile ``next`` is neutralised — LoginView
    validates the target host, so login falls back to LOGIN_REDIRECT_URL."""
    user = UserFactory()

    response = client.post(
        "/login",
        {
            "username": user.email,
            "password": DEFAULT_PASSWORD,
            "next": "https://evil.example/steal",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == "/"
    assert "evil.example" not in response["Location"]


def test_logout_ends_session_and_redirects_to_login(client):
    user = UserFactory()
    client.force_login(user)
    assert "_auth_user_id" in client.session

    response = client.post("/logout")

    assert response.status_code == 302
    assert response["Location"] == "/login"
    assert "_auth_user_id" not in client.session


def test_landing_requires_authentication(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_landing_dispatches_authenticated_user_to_role_home(client):
    # S1 turned `/` into role dispatch; UserFactory defaults to technician.
    user = UserFactory()
    client.force_login(user)

    response = client.get("/")

    assert response.status_code == 302
    assert response["Location"] == "/tech"
