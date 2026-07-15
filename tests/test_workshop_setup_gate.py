"""Workshop setup-gate + setup page (A2). Acceptance criteria = test contract
(slice_map §4, rewritten per D-126): the per-user gate, the setup page, the
per-admin one-workshop guard, and workshop isolation.

Runs against PostgreSQL (config.settings.test) — never SQLite.
"""

import pytest

from catalog.models import OperationType, Workshop
from tests.factories import UserFactory, WorkshopFactory
from workshop.forms import WorkshopSetupForm
from workshop.services import WorkshopExistsError, create_workshop

pytestmark = pytest.mark.django_db


# --- Criterion 1: workshop-less admin is gated to setup from any route -------


@pytest.mark.parametrize("path", ["/admin", "/profile", "/"])
def test_gate_redirects_workshopless_admin_from_any_route(client, path):
    admin = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin)

    response = client.get(path)

    assert response.status_code == 302
    assert response["Location"] == "/workshop/setup"


def test_gate_fires_even_when_other_workshops_exist(client):
    # Another admin has already set up their own workshop — it must have no
    # bearing on this admin's gate (per-user, never a global Workshop count).
    other = UserFactory(account_role="admin")
    assert other.workshop_id is not None

    admin = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin)

    response = client.get("/admin")

    assert response.status_code == 302
    assert response["Location"] == "/workshop/setup"


def test_admin_with_workshop_passes_the_gate(client):
    admin = UserFactory(account_role="admin")  # factory assigns a workshop
    client.force_login(admin)

    assert client.get("/admin").status_code == 200


# --- Criterion 2: setup creates the workshop, releases the gate, guards 2nd --


def test_setup_creates_workshop_and_releases_gate(client):
    admin = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin)

    response = client.post(
        "/workshop/setup",
        {"name": "Bench & Board", "address": "5 Timber Lane", "email": "hi@bb.test"},
    )

    assert response.status_code == 302
    assert response["Location"] == "/"
    admin.refresh_from_db()
    assert admin.workshop is not None
    assert admin.workshop.name == "Bench & Board"
    # Gate no longer fires — a normal route passes through.
    assert client.get("/admin").status_code == 200


def test_second_create_workshop_by_same_admin_is_refused():
    # Per-admin guard at the service level (not a global Workshop count).
    admin = UserFactory(account_role="admin", workshop=None)
    first = WorkshopSetupForm(
        data={"name": "First", "address": "1 A St", "email": "a@a.test"}
    )
    assert first.is_valid(), first.errors
    create_workshop(first, admin)

    second = WorkshopSetupForm(
        data={"name": "Second", "address": "2 B St", "email": "b@b.test"}
    )
    assert second.is_valid(), second.errors
    with pytest.raises(WorkshopExistsError):
        create_workshop(second, admin)

    assert admin.workshop.name == "First"
    assert not Workshop.objects.filter(name="Second").exists()


def test_one_admins_setup_does_not_affect_another_admins_gate(client):
    admin_a = UserFactory(account_role="admin", workshop=None)
    form_a = WorkshopSetupForm(
        data={"name": "A Shop", "address": "1 A St", "email": "a@a.test"}
    )
    assert form_a.is_valid(), form_a.errors
    create_workshop(form_a, admin_a)

    admin_b = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin_b)

    response = client.get("/admin")
    assert response.status_code == 302
    assert response["Location"] == "/workshop/setup"


# --- Criterion 3: after setup the admin reaches the dashboard ----------------


def test_after_setup_admin_reaches_dashboard(client):
    admin = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin)
    client.post(
        "/workshop/setup",
        {"name": "WS", "address": "1 St", "email": "w@w.test"},
    )

    root = client.get("/")
    assert root.status_code == 302
    assert root["Location"] == "/admin"
    assert client.get("/admin").status_code == 200


# --- Criterion 4: gate excludes setup / logout / static (no redirect loop) ---


def test_setup_route_itself_is_not_gated(client):
    admin = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin)

    response = client.get("/workshop/setup")

    assert response.status_code == 200  # the form, not a redirect back to itself


def test_logout_is_not_gated(client):
    admin = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin)

    response = client.post("/logout")

    assert response.status_code == 302
    assert response["Location"] == "/login"


def test_static_prefix_is_not_gated(client):
    admin = UserFactory(account_role="admin", workshop=None)
    client.force_login(admin)

    response = client.get("/static/css/base.css")

    # Whatever the resolver does with it (404 in tests — static isn't served),
    # it must NOT be the gate's 302 back to setup.
    assert not (
        response.status_code == 302 and response["Location"] == "/workshop/setup"
    )


# --- Criterion 5: unauthenticated users still go to login --------------------


def test_unauthenticated_setup_route_redirects_to_login(client):
    response = client.get("/workshop/setup")
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_unauthenticated_normal_route_redirects_to_login(client):
    response = client.get("/admin")
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


# --- Design note: a non-admin at the setup route gets a clean 403 ------------


def test_setup_refuses_non_admin_with_403(client):
    user = UserFactory(account_role="manager", workshop=None)
    client.force_login(user)

    response = client.get("/workshop/setup")

    assert response.status_code == 403


# --- Criterion 7: two workshops hold identically-named library rows ----------


def test_two_workshops_hold_identically_named_library_rows():
    ws_a = WorkshopFactory()
    ws_b = WorkshopFactory()

    op_a = OperationType.objects.create(workshop=ws_a, name="Cutting")
    op_b = OperationType.objects.create(workshop=ws_b, name="Cutting")

    assert op_a.pk != op_b.pk
    assert OperationType.objects.filter(name="Cutting").count() == 2
