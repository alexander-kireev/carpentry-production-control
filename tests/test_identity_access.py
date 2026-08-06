from datetime import date

import pytest

from identity.models import User
from identity.queries import resolve_authenticated_destination
from identity.results import Destination

pytestmark = pytest.mark.django_db


def unattached_admin():
    return User.objects.create_user(
        email="admin@example.test",
        password="Valid-password-483!",
        first_name="A",
        last_name="Dmin",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )


def test_exact_unattached_admin_resolves_from_fresh_authority_envelope(
    django_assert_num_queries,
):
    user = unattached_admin()
    with django_assert_num_queries(1):
        result = resolve_authenticated_destination(user)
    assert result.supported
    assert result.destination == Destination.CREATE_WORKSHOP


def test_direct_routes_redirect_before_page_resolution(client):
    user = unattached_admin()
    client.force_login(user)
    response = client.get("/operations?next=/unsafe")
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/workshop"


def test_anonymous_direct_route_goes_to_login(client):
    assert client.get("/operations").headers["Location"] == "/login"


def test_invitation_route_bypasses_session_tenant_guard(client):
    user = unattached_admin()
    client.force_login(user)
    response = client.get(f"/invitations/999/{'x' * 43}")
    assert response.status_code == 404
    assert b"This invitation is unavailable" in response.content
