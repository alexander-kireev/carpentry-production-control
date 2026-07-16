"""Profile page (D2) — per-role rendering, the self-service phone write, the
inert clearance button, and avatar determinism.

Runs against PostgreSQL (config.settings.test) — never SQLite. The "Admin"
WorkshopRole sentinel is seeded by the D0-3 migration, so it exists in the test
database and an admin fixture reuses it (rather than a factory-made role) to
exercise the real sentinel resolution.
"""

import pytest

from accounts.models import AVATAR_PALETTE, User
from catalog.models import WorkshopRole
from catalog.seeds import ADMIN_ROLE_NAME
from tests.factories import OperationTypeFactory, UserFactory

pytestmark = pytest.mark.django_db

NON_ADMIN_ROLES = ["manager", "operator", "technician"]


def admin_sentinel_role():
    """The permanent workshop-independent "Admin" WorkshopRole (D0-3 seed)."""
    return WorkshopRole.objects.get(workshop__isnull=True, name=ADMIN_ROLE_NAME)


def make_admin():
    """An admin whose workshop role is the seeded "Admin" sentinel."""
    return UserFactory(account_role="admin", workshop_role=admin_sentinel_role())


# --- Rendering: shared shape ---------------------------------------------


def test_profile_requires_login(client):
    response = client.get("/profile")
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


@pytest.mark.parametrize("role", ["admin", *NON_ADMIN_ROLES])
def test_profile_renders_for_each_role(client, role):
    user = make_admin() if role == "admin" else UserFactory(account_role=role)
    client.force_login(user)
    response = client.get("/profile")
    assert response.status_code == 200
    content = response.content.decode()
    assert user.email in content
    # Identity card membership line is present for every role.
    assert "Member since" in content


@pytest.mark.parametrize("role", ["admin", *NON_ADMIN_ROLES])
def test_profile_has_no_leaked_template_comments(client, role):
    # Multi-line {# #} is not a valid Django comment (no re.DOTALL) and leaks as
    # literal text; the profile uses {% comment %} — guard it, per the S1 note.
    user = make_admin() if role == "admin" else UserFactory(account_role=role)
    client.force_login(user)
    content = client.get("/profile").content.decode()
    assert "{#" not in content
    assert "{% comment %}" not in content


# --- Identity: no account-role badge, no editable identity fields ---------


def test_no_account_role_badge(client):
    # The account role ("Operator") must not be rendered on Profile (D-124). The
    # user's workshop role and clearances are named so they can't collide with it.
    user = UserFactory(account_role="operator", workshop_role__name="Bench Joiner")
    client.force_login(user)
    content = client.get("/profile").content.decode()
    assert "Bench Joiner" in content  # workshop role IS shown
    assert user.get_account_role_display() not in content  # account role is NOT


@pytest.mark.parametrize("role", ["admin", *NON_ADMIN_ROLES])
def test_identity_fields_are_read_only(client, role):
    # first/last/DOB and email display as text — no edit input carries their
    # value (identity editing is D3; email has no edit path at all).
    user = make_admin() if role == "admin" else UserFactory(account_role=role)
    user.first_name, user.last_name = "Jan", "Novak"
    user.save()
    client.force_login(user)
    content = client.get("/profile").content.decode()
    assert "Jan" in content and "Novak" in content
    assert f'value="{user.first_name}"' not in content
    assert f'value="{user.last_name}"' not in content
    assert f'value="{user.email}"' not in content
    assert 'type="email"' not in content  # email has no edit control
    assert "Read-only" in content  # email carries the read-only badge


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_admin_managed_label_shown_for_non_admins(client, role):
    client.force_login(UserFactory(account_role=role))
    assert "Admin managed" in client.get("/profile").content.decode()


def test_admin_managed_label_absent_for_admin(client):
    client.force_login(make_admin())
    assert "Admin managed" not in client.get("/profile").content.decode()


# --- Role & skills: workshop role, clearances, inert button ---------------


def test_admin_workshop_role_resolves_to_sentinel(client):
    # Not a hardcoded "Admin" string — it comes from the seeded sentinel row.
    admin = make_admin()
    assert admin.workshop_role.name == ADMIN_ROLE_NAME
    client.force_login(admin)
    assert "Admin" in client.get("/profile").content.decode()


def test_non_admin_shows_clearances_and_inert_button(client):
    user = UserFactory(account_role="operator", workshop_role__name="Bench Joiner")
    user.clearances.add(
        OperationTypeFactory(workshop=user.workshop, name="Cutting"),
        OperationTypeFactory(workshop=user.workshop, name="Assembly"),
    )
    client.force_login(user)
    content = client.get("/profile").content.decode()
    assert "Cutting" in content and "Assembly" in content
    # The button is present but inert — a disabled span, no record, no modal.
    assert 'aria-disabled="true">Request clearance change' in content


def test_admin_has_no_clearances_or_clearance_button(client):
    client.force_login(make_admin())
    content = client.get("/profile").content.decode()
    assert "Request clearance change" not in content
    assert "Op types cleared" not in content


# --- Personal information: the one live self-service write (phone) ---------


def test_phone_save_persists_and_redirects(client):
    user = UserFactory(account_role="manager")
    client.force_login(user)
    response = client.post("/profile", {"phone": "+44 20 7946 0000"})
    assert response.status_code == 302
    assert response["Location"] == "/profile"
    user.refresh_from_db()
    assert user.phone == "+44 20 7946 0000"


def test_phone_can_be_cleared(client):
    user = UserFactory(account_role="manager", phone="+44 20 7946 0000")
    client.force_login(user)
    response = client.post("/profile", {"phone": ""})
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.phone == ""


def test_phone_edit_form_is_the_only_editable_field(client):
    # The one edit control on the page is the phone input (type="tel").
    client.force_login(UserFactory(account_role="operator"))
    content = client.get("/profile").content.decode()
    assert 'type="tel"' in content


# --- Deferred cards are not built (AC) ------------------------------------


@pytest.mark.parametrize("role", ["admin", *NON_ADMIN_ROLES])
def test_deferred_cards_absent(client, role):
    user = make_admin() if role == "admin" else UserFactory(account_role=role)
    client.force_login(user)
    content = client.get("/profile").content.decode()
    for label in (
        "Two-factor authentication",
        "Notification preferences",
        "Active sessions",
        "Change password",
    ):
        assert label not in content


# --- Avatar: derived + deterministic --------------------------------------


def test_avatar_initials_from_name():
    user = UserFactory.build(first_name="Jan", last_name="Novak")
    assert user.avatar_initials == "JN"


def test_avatar_initials_fall_back_to_email():
    user = UserFactory.build(first_name="", last_name="", email="zoe@example.com")
    assert user.avatar_initials == "Z"


def test_avatar_colour_is_deterministic_and_in_palette():
    user = UserFactory.build(email="same.person@example.com")
    first = user.avatar_colour
    assert first in AVATAR_PALETTE
    assert first == user.avatar_colour  # stable across accesses
    # Same email → same slot on a distinct instance (no stored image, no salt).
    twin = User(email="same.person@example.com")
    assert twin.avatar_colour == first


def test_avatar_colour_renders_on_page(client):
    user = UserFactory(account_role="operator")
    client.force_login(user)
    content = client.get("/profile").content.decode()
    assert f"background:{user.avatar_colour}" in content
