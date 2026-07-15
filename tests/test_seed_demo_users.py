"""Dev-only ``seed_demo_users`` management command (seed_demo_users ticket).

The command seeds a demo Workshop and one active login per account_role so the
Slice-S shell can be exercised before Slice B's invitation flow. These assert it
creates the expected Workshop + four active, authenticatable users, that a re-run
is idempotent, and that it refuses to run outside DEBUG.

pytest-django applies migrations when building the test DB, so the D0-3 system
seeds (the "Admin"/"undefined" WorkshopRole sentinels the command assigns) are
already present. The command is DEBUG-gated, so the happy-path tests flip
``settings.DEBUG`` on via the pytest-django ``settings`` fixture.
"""

import pytest
from django.contrib.auth import authenticate
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import User
from catalog.models import Workshop
from catalog.seeds import ADMIN_ROLE_NAME, UNDEFINED_NAME

pytestmark = pytest.mark.django_db

# Mirror of the command's demo logins: email -> (account_role, workshop_role name,
# password). Kept here (not imported from the command) so the test pins the
# contract independently of the command's internals.
EXPECTED = {
    "admin@demo.local": (User.AccountRole.ADMIN, ADMIN_ROLE_NAME, "demo-admin-pass"),
    "manager@demo.local": (
        User.AccountRole.MANAGER,
        UNDEFINED_NAME,
        "demo-manager-pass",
    ),
    "operator@demo.local": (
        User.AccountRole.OPERATOR,
        UNDEFINED_NAME,
        "demo-operator-pass",
    ),
    "technician@demo.local": (
        User.AccountRole.TECHNICIAN,
        UNDEFINED_NAME,
        "demo-technician-pass",
    ),
}


def test_seeds_workshop_and_four_active_role_users(settings):
    settings.DEBUG = True
    call_command("seed_demo_users")

    workshop = Workshop.objects.get()  # exactly one
    assert User.objects.count() == len(EXPECTED)

    for email, (account_role, role_name, _password) in EXPECTED.items():
        user = User.objects.get(email=email)
        assert user.account_role == account_role
        assert user.status == User.Status.ACTIVE
        assert user.workshop_id == workshop.id
        assert user.workshop_role.name == role_name
        # The assigned role is the D0-3 workshop-independent system sentinel.
        assert user.workshop_role.workshop_id is None


def test_seeded_users_authenticate_by_email(settings):
    settings.DEBUG = True
    call_command("seed_demo_users")

    for email, (_role, _role_name, password) in EXPECTED.items():
        assert authenticate(email=email, password=password) is not None


def test_re_run_is_idempotent(settings):
    settings.DEBUG = True
    call_command("seed_demo_users")
    call_command("seed_demo_users")

    assert Workshop.objects.count() == 1
    assert User.objects.count() == len(EXPECTED)


def test_refuses_to_run_outside_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_demo_users")

    # Guard fires before anything is created.
    assert User.objects.count() == 0
    assert Workshop.objects.count() == 0
