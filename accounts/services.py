"""Accounts service layer (introduced here — A1).

Business logic that shouldn't live in a view lands here, request-agnostic, so
it stays callable from a test or a management command as well as an HTTP
request. Later slices (B, D, ...) add their own functions to this module
rather than putting service logic directly in views.
"""

from django.db import transaction

from accounts.models import User
from catalog.models import WorkshopRole
from catalog.seeds import ADMIN_ROLE_NAME


class AdminExistsError(Exception):
    """Raised by register_admin when an admin account already exists.

    The one-admin guard lives here, not only in the view, so it holds even if
    a caller reaches this function without going through the view's own
    pre-check (e.g. a race between two concurrent submissions).
    """


def register_admin(form) -> User:
    """Create the single self-registering admin from a validated form.

    Assigns ``account_role=admin``, ``status=active``, and the D0-3 seeded
    "Admin" ``WorkshopRole``. ``User.workshop`` is left null (set at A2).
    Does not log the user in — that's a session/HTTP concern the caller
    (the view) handles with the returned user.
    """
    if User.objects.filter(account_role=User.AccountRole.ADMIN).exists():
        raise AdminExistsError

    with transaction.atomic():
        admin_role = WorkshopRole.objects.get(
            workshop__isnull=True, name=ADMIN_ROLE_NAME
        )
        user = form.save(commit=False)
        user.account_role = User.AccountRole.ADMIN
        user.status = User.Status.ACTIVE
        user.workshop_role = admin_role
        user.save()

    return user
