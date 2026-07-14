"""S1 route-200 smoke: role dispatch, every S1 route, and the DEBUG switcher.

Extended per-role by S2-S5 as each role's pages land. Runs against PostgreSQL
(config.settings.test) — never SQLite.
"""

import pytest
from django.test import override_settings

from accounts.models import User
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

# Role -> its landing path (flat routes, no trailing slash; D-123/CHG-054).
LANDINGS = {
    "admin": "/admin",
    "manager": "/manager",
    "operator": "/operator",
    "technician": "/tech",
}


@pytest.mark.parametrize("role, landing", LANDINGS.items())
def test_root_dispatches_by_account_role(client, role, landing):
    client.force_login(UserFactory(account_role=role))
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == landing


@pytest.mark.parametrize("role, landing", LANDINGS.items())
def test_role_landing_returns_200_for_its_role(client, role, landing):
    client.force_login(UserFactory(account_role=role))
    assert client.get(landing).status_code == 200


def test_profile_returns_200(client):
    client.force_login(UserFactory(account_role="manager"))
    assert client.get("/profile").status_code == 200


def test_analytics_placeholder_returns_200(client):
    client.force_login(UserFactory(account_role="operator"))
    assert client.get("/analytics").status_code == 200


def test_unauthenticated_route_redirects_to_login(client):
    response = client.get("/admin")
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


# --- DEBUG "view as role" switcher ---------------------------------------


@override_settings(DEBUG=True)
def test_debug_switcher_changes_effective_dispatch(client):
    client.force_login(UserFactory(account_role="admin"))

    switched = client.post("/debug/view-as", {"role": "operator"})
    assert switched.status_code == 302

    # root now dispatches by the overridden effective role, not account_role.
    assert client.get("/")["Location"] == "/operator"


@override_settings(DEBUG=True)
def test_debug_switcher_reset_restores_account_role(client):
    client.force_login(UserFactory(account_role="admin"))
    client.post("/debug/view-as", {"role": "operator"})

    client.post("/debug/view-as", {"role": ""})  # empty/invalid clears override

    assert client.get("/")["Location"] == "/admin"


@override_settings(DEBUG=True)
def test_debug_switcher_ignores_invalid_role(client):
    client.force_login(UserFactory(account_role="admin"))
    client.post("/debug/view-as", {"role": "wizard"})
    assert client.get("/")["Location"] == "/admin"


def test_debug_switcher_inert_when_not_debug(client):
    # config.settings.test has DEBUG=False.
    client.force_login(UserFactory(account_role="admin"))

    blocked = client.post("/debug/view-as", {"role": "operator"})
    assert blocked.status_code == 404

    # Dispatch still follows the real account role.
    assert client.get("/")["Location"] == "/admin"


def test_effective_role_ignores_stale_override_in_production(client):
    """A session override left over must not affect dispatch when DEBUG=False."""
    client.force_login(UserFactory(account_role="admin"))
    session = client.session
    session["debug_override_role"] = "operator"
    session.save()

    assert client.get("/")["Location"] == "/admin"


def test_all_account_roles_have_a_landing():
    """Guard: every AccountRole is wired into dispatch (no silent 400s)."""
    from shell.roles import ROLE_LANDING

    assert set(User.AccountRole.values) == set(ROLE_LANDING)
