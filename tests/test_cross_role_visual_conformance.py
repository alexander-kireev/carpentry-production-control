import pytest
from django.test import Client

from identity.models import User
from tests.test_library_commands import library_admin
from tests.test_onboarding_http import admin as onboarding_admin
from workshops.models import Workshop

pytestmark = pytest.mark.django_db(transaction=True)


def _operational_people(suffix):
    admin, workshop = library_admin(suffix)
    manager_role = workshop.roles.create(name="Manager")
    operator_role = workshop.roles.create(name="Operator")
    manager = User.objects.create_user(
        email=f"manager+{suffix}@example.test",
        password="test-only-password",
        first_name="Morgan",
        last_name="Manager",
        date_of_birth="1991-05-18",
        account_role=User.AccountRole.MANAGER,
        workshop=workshop,
        workshop_role=manager_role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    operator = User.objects.create_user(
        email=f"operator+{suffix}@example.test",
        password="test-only-password",
        first_name="Olivia",
        last_name="Operator",
        date_of_birth="1992-06-19",
        account_role=User.AccountRole.OPERATOR,
        workshop=workshop,
        workshop_role=operator_role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    workshop.status = Workshop.Status.OPERATIONAL
    workshop.save(update_fields=("status",))
    return admin, manager, operator


def _get_as(client, user, path):
    client.force_login(user)
    response = client.get(path)
    return response, response.content.decode("utf-8")


def test_operational_roles_share_shell_but_keep_exact_capabilities():
    admin, manager, operator = _operational_people("visual-roles")
    client = Client()

    admin_response, admin_html = _get_as(client, admin, "/workshop/libraries")
    manager_response, manager_html = _get_as(client, manager, "/workshop/libraries")
    for response, html in (
        (admin_response, admin_html),
        (manager_response, manager_html),
    ):
        assert response.status_code == 200
        assert 'class="appbar"' in html
        assert 'class="workshop-subnav"' in html
        assert 'class="catalogue"' in html
        assert "Libraries" in html and "Materials" in html and "Stations" in html

    assert "Add role" in admin_html and "Add from presets · SC-04" in admin_html
    assert "Read-only" in manager_html
    assert (
        "Add role" not in manager_html
        and "Add from presets · SC-04" not in manager_html
    )

    denied, denied_html = _get_as(client, operator, "/workshop/libraries")
    assert denied.status_code == 302
    assert "Libraries" not in denied_html
    materials, operator_html = _get_as(client, operator, "/workshop/materials")
    assert materials.status_code == 200
    assert 'class="appbar"' in operator_html
    assert 'class="workshop-subnav"' in operator_html
    assert "Libraries" not in operator_html
    assert "Add material" not in operator_html


def test_onboarding_contact_default_is_explained_without_disabling_field():
    admin = onboarding_admin()
    client = Client()
    client.force_login(admin)
    response = client.get("/onboarding/workshop")
    html = response.content.decode("utf-8")
    assert response.status_code == 200
    assert (
        "Prefilled from your account. Change this if the Workshop uses a shared or different contact address."
        in html
    )
    assert 'name="contact_email"' in html
    assert 'name="contact_email" disabled' not in html


def test_shared_css_uses_canonical_graphite_anatomy(settings):
    css = (settings.BASE_DIR / "static/css/foundation.css").read_text(
        encoding="utf-8", errors="strict"
    )
    for contract in (
        "--surface-0: #f4f4f5",
        "--surface-2: #fff",
        ".appbar",
        ".workshop-subnav",
        ".filter-toolbar",
        ".library-family",
        ".dialog",
        "@media (max-width: 45rem)",
    ):
        assert contract in css
    assert ".role-nav a { color: #fff; }" not in css
    assert ".library-shell h1" not in css
