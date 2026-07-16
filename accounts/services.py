"""Accounts service layer (introduced in A1; opened up in A2 per D-126).

Business logic that shouldn't live in a view lands here, request-agnostic, so
it stays callable from a test or a management command as well as an HTTP
request. Later slices (B, D, ...) add their own functions to this module
rather than putting service logic directly in views.
"""

from django.db import transaction

from accounts.models import User
from catalog.models import WorkshopRole
from catalog.seeds import ADMIN_ROLE_NAME


def register_admin(form) -> User:
    """Create a self-registering admin from a validated form.

    Assigns ``account_role=admin``, ``status=active``, and the D0-3 seeded
    "Admin" ``WorkshopRole``; ``User.workshop`` is left null until the admin
    completes Workshop setup (A2). Registration is always open — the instance
    hosts many independent admins/workshops (D-126), so there is no one-admin
    guard. Does not log the user in — that session/HTTP concern is the caller's
    (the view's) job, using the returned user.
    """
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


def set_own_phone(user: User, phone: str) -> User:
    """Persist a user's self-service phone number (Slice D).

    Phone is the one profile field the owner edits directly, with no approval
    step (see User behaviour, Business rules). Request-agnostic like
    ``register_admin`` — takes the domain value, writes only the ``phone``
    column, and returns the user; the caller owns the HTTP/redirect concern.
    """
    user.phone = phone
    user.save(update_fields=["phone"])
    return user
