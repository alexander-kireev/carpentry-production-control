"""Admin self-registration (A1): register form, ``register_admin`` service, the
one-admin guard, and the password policy. Acceptance criteria = test contract
(slice_map §4). Criterion 5 (unauthenticated -> login redirect) is existing F2
coverage in ``test_auth.py`` and isn't duplicated here.
"""

import pytest

from accounts.forms import AdminRegisterForm
from accounts.models import User
from accounts.services import AdminExistsError, register_admin
from catalog.models import WorkshopRole
from catalog.seeds import ADMIN_ROLE_NAME

pytestmark = pytest.mark.django_db

VALID_PASSWORD = "Sturdy-Bench-42"


def _valid_post_data(**overrides):
    data = {
        "first_name": "Jamie",
        "last_name": "Carter",
        "date_of_birth": "1985-06-15",
        "email": "jamie.carter@example.com",
        "password1": VALID_PASSWORD,
        "password2": VALID_PASSWORD,
    }
    data.update(overrides)
    return data


# --- Criterion 1: fresh-DB registration -------------------------------------


def test_register_creates_active_admin_with_seeded_role_and_session(client):
    response = client.post("/register", _valid_post_data())

    assert response.status_code == 302
    user = User.objects.get(email="jamie.carter@example.com")
    assert user.account_role == User.AccountRole.ADMIN
    assert user.status == User.Status.ACTIVE
    assert user.workshop_role.name == ADMIN_ROLE_NAME
    assert user.workshop_role.workshop_id is None
    # User.workshop stays null here — set at A2 (out of scope for A1).
    assert user.workshop_id is None
    assert client.session.get("_auth_user_id") == str(user.pk)


# --- Criterion 4: redirect to /workshop/setup -------------------------------


def test_register_redirects_to_workshop_setup(client):
    response = client.post("/register", _valid_post_data())

    assert response.status_code == 302
    assert response["Location"] == "/workshop/setup"


# --- Criterion 2: password policy -------------------------------------------


def test_register_get_renders_form_with_password_hint(client):
    response = client.get("/register")

    assert response.status_code == 200
    assert response.context["locked"] is False
    assert b"at least 10 characters" in response.content.lower()


def test_password_too_short_rejected_with_form_error(client):
    # 9 characters: distinguishes the required min_length=10 from Django's
    # unconfigured default of 8, which would wrongly let this one through.
    response = client.post(
        "/register", _valid_post_data(password1="Abcxyz123", password2="Abcxyz123")
    )

    assert response.status_code == 200
    assert "password1" in response.context["form"].errors
    assert not User.objects.filter(email="jamie.carter@example.com").exists()


def test_password_common_rejected_with_form_error(client):
    response = client.post(
        "/register",
        _valid_post_data(
            first_name="Casey",
            last_name="Nolan",
            email="casey.nolan@example.com",
            password1="basketball",
            password2="basketball",
        ),
    )

    assert response.status_code == 200
    assert "password1" in response.context["form"].errors
    assert not User.objects.filter(email="casey.nolan@example.com").exists()


def test_password_similar_to_name_or_email_rejected_with_form_error(client):
    response = client.post(
        "/register",
        _valid_post_data(
            first_name="Jordan",
            last_name="Baker",
            email="jordanbaker@example.com",
            password1="jordanbaker99",
            password2="jordanbaker99",
        ),
    )

    assert response.status_code == 200
    assert "password1" in response.context["form"].errors
    assert not User.objects.filter(email="jordanbaker@example.com").exists()


def test_password_all_numeric_rejected_with_form_error(client):
    response = client.post(
        "/register",
        _valid_post_data(password1="4728195036", password2="4728195036"),
    )

    assert response.status_code == 200
    assert "password1" in response.context["form"].errors
    assert not User.objects.filter(email="jamie.carter@example.com").exists()


def test_valid_password_is_accepted(client):
    response = client.post("/register", _valid_post_data())

    assert response.status_code == 302
    assert User.objects.filter(email="jamie.carter@example.com").exists()


def test_mismatched_password_confirmation_rejected(client):
    response = client.post(
        "/register",
        _valid_post_data(password1=VALID_PASSWORD, password2="something-else-99"),
    )

    assert response.status_code == 200
    assert "password2" in response.context["form"].errors
    assert not User.objects.filter(email="jamie.carter@example.com").exists()


# --- Criterion 3: one-admin guard --------------------------------------------


def test_register_view_locks_once_an_admin_exists(client):
    client.post("/register", _valid_post_data())

    response = client.post(
        "/register",
        _valid_post_data(
            first_name="Alex", last_name="Doe", email="alex.doe@example.com"
        ),
    )

    assert response.status_code == 200
    assert response.context["locked"] is True
    assert not User.objects.filter(email="alex.doe@example.com").exists()


def test_register_get_locks_once_an_admin_exists(client):
    client.post("/register", _valid_post_data())

    response = client.get("/register")

    assert response.status_code == 200
    assert response.context["locked"] is True


def test_register_admin_service_refuses_when_admin_already_exists():
    # Proves the guard lives in the service itself, not only the view's
    # pre-check — calling register_admin twice directly, bypassing /register.
    first_form = AdminRegisterForm(data=_valid_post_data())
    assert first_form.is_valid(), first_form.errors
    register_admin(first_form)

    second_form = AdminRegisterForm(
        data=_valid_post_data(
            first_name="Alex", last_name="Doe", email="alex.doe@example.com"
        )
    )
    assert second_form.is_valid(), second_form.errors
    with pytest.raises(AdminExistsError):
        register_admin(second_form)

    assert not User.objects.filter(email="alex.doe@example.com").exists()


def test_register_admin_assigns_seeded_admin_role_directly():
    form = AdminRegisterForm(data=_valid_post_data())
    assert form.is_valid(), form.errors

    user = register_admin(form)

    assert user.account_role == User.AccountRole.ADMIN
    assert user.status == User.Status.ACTIVE
    assert user.workshop_role == WorkshopRole.objects.get(
        workshop__isnull=True, name=ADMIN_ROLE_NAME
    )
