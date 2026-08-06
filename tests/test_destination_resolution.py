from datetime import date

import pytest

from identity.models import User
from identity.queries import (
    get_pending_manager_setup,
    resolve_authenticated_destination,
)
from identity.results import Destination
from workshops.models import Workshop, WorkshopRole

pytestmark = pytest.mark.django_db


def workshop(status="manager_required"):
    return Workshop.objects.create(
        name="Destination",
        address="Address",
        email="dest@example.test",
        timezone="UTC",
        status=status,
    )


def attached(*, account_role, status="manager_required", role=None):
    tenant = workshop(status)
    role = role or WorkshopRole.objects.get(
        machine_key="admin" if account_role == "admin" else "undefined"
    )
    return User.objects.create_user(
        email=f"{account_role}-{status}@example.test",
        password="Valid-password-483!",
        first_name="Route",
        last_name="User",
        date_of_birth=date(1990, 1, 1),
        account_role=account_role,
        status="active",
        onboarding_state=None,
        workshop=tenant,
        workshop_role=role,
    )


@pytest.mark.parametrize(
    ("account_role", "status", "destination"),
    [
        ("admin", "manager_required", Destination.INVITE_MANAGER),
        ("admin", "manager_activation_pending", Destination.SETUP_COCKPIT),
        ("operator", "manager_required", Destination.HOLDING),
        ("operator", "manager_activation_pending", Destination.HOLDING),
        ("admin", "operational", Destination.DASHBOARD),
        ("manager", "operational", Destination.DASHBOARD),
        ("operator", "operational", Destination.DASHBOARD),
    ],
)
def test_destination_classes(account_role, status, destination):
    assert (
        resolve_authenticated_destination(
            attached(account_role=account_role, status=status)
        ).destination
        == destination
    )


def test_cross_tenant_role_and_preoperational_manager_fail_closed():
    foreign = workshop()
    foreign_role = WorkshopRole.objects.create(
        workshop=foreign, name="Machinist", status="active"
    )
    cross = attached(account_role="operator")
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE user_account DISABLE TRIGGER cst_014_022_026_664_user_write_guard"
        )
        cursor.execute(
            "UPDATE user_account SET workshop_role_id=%s WHERE id=%s",
            [foreign_role.id, cross.id],
        )
    manager = attached(account_role="manager")
    assert not resolve_authenticated_destination(cross).supported
    assert not resolve_authenticated_destination(manager).supported


@pytest.mark.django_db(transaction=True)
def test_pending_projection_fails_closed_when_aggregate_is_incomplete():
    from identity.commands import invite_permanent_manager
    from identity.models import EmailDeliveryIntent
    from tests.test_manager_invitation import attached_admin, payload

    admin, _, _ = attached_admin(email="projection@example.test")
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="projection"
    )
    admin.refresh_from_db()
    assert get_pending_manager_setup(admin)["delivery_status"] == "sent"
    EmailDeliveryIntent.objects.all().delete()
    assert get_pending_manager_setup(admin) is None
