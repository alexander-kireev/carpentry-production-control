"""Notification services (N1).

The Notification mechanism's request-agnostic surface (the A1 pattern: plain
domain inputs, no ``request``, no session/HTTP), so any caller — N3's Slice-D
services, later Slices B and E, management commands, tests — fans out
notifications the same way.

Three pieces:

* ``notify()`` — the fan-out helper (one record per deduped recipient).
* ``active_managers()`` — the standard manager recipient set for a workshop.
* the Linked Context source **registry** — a dispatch keyed on ``source_type``
  that resolves a live summary of the triggering object for the Notifications
  page's Linked Context panel (rendered by N2).
"""

from collections.abc import Callable, Iterable

from django.db import models

from accounts.models import ChangeRequest, User
from notifications.models import Notification


def notify(
    recipients: Iterable[User],
    *,
    category: str,
    title: str,
    body: str | None = None,
    source: models.Model | None = None,
) -> list[Notification]:
    """Create one Notification per recipient (the fan-out helper).

    Request-agnostic and deliberately "dumb": it fans out to exactly the
    recipients it is given and derives the source pointer — nothing more.
    **Recipient computation and actor-silence are the caller's job** (N3 / B / E),
    so every future trigger controls its own audience.

    Args:
        recipients: an iterable of ``User`` instances. Deduplicated by pk, so a
            user appearing twice still receives a single record.
        category: a ``Notification.Category`` value.
        title: short summary line.
        body: optional detail (nullable).
        source: optional model instance. When given, ``source_type`` is set to
            its model name and ``source_id`` to ``str(instance.pk)``; the pair is
            left NULL for a system-wide notification with no single source.

    Returns:
        The list of created ``Notification`` records (one per deduped recipient).

    Wired call sites (D-127). This docstring is the single forward-reference for
    the reserved-but-unwired categories — N1 adds no stub call sites:

    * **N3** (via Slice D's ``accounts.services``): ``account`` (identity CR
      approved, and the admin's own-profile name/DOB self-edit) and
      ``cr_rejected`` (identity CR rejected). These are the only categories with
      a live source once Slice D has landed.
    * **Slice B** (User management + invitations): ``clearance_changed`` and
      ``account`` (admin direct-edit of clearances / name / DOB), plus
      ``invite_accepted`` / ``invite_expired``.
    * **Slice E** (Purchase Orders): ``po_arrived`` / ``po_cancelled`` and the
      stock crossings they drive, ``stock_out`` / ``stock_replenished``.

    Every other ``Category`` value is dormant this phase — its source object
    (Station, Operation, ClearanceRequest workflow, LeaveRequest, Order,
    WorkItem, Report, Messaging) is a Phase 2+ shell with no live event to fire.
    """
    source_type, source_id = _derive_source(source)
    deduped: dict[object, User] = {}
    for recipient in recipients:
        deduped.setdefault(recipient.pk, recipient)
    return [
        Notification.objects.create(
            recipient=recipient,
            category=category,
            title=title,
            body=body,
            source_type=source_type,
            source_id=source_id,
        )
        for recipient in deduped.values()
    ]


def _derive_source(source: models.Model | None) -> tuple[str | None, str | None]:
    """(source_type, source_id) for a model instance, or (None, None)."""
    if source is None:
        return None, None
    return type(source).__name__, str(source.pk)


def active_managers(workshop) -> models.QuerySet:
    """Active managers of ``workshop`` — the standard manager recipient set.

    The first/only-manager stopgap's audience (one manager per workshop in MVP,
    per the development strategy's assumptions), reused by later B/E triggers that
    notify "the manager(s)". Returns a workshop-scoped queryset.
    """
    return User.objects.filter(
        workshop=workshop,
        account_role=User.AccountRole.MANAGER,
        status=User.Status.ACTIVE,
    )


# --------------------------------------------------------------------------- #
# Linked Context source registry
# --------------------------------------------------------------------------- #
#
# A dispatch keyed on ``source_type`` (the model name stored on the
# Notification), mapping to a resolver that computes a LIVE summary of the
# triggering object from ``source_type`` + ``source_id`` — nothing is stored on
# the Notification itself. Built as a registry, not a hardcoded if/else, so later
# slices register their own source types with no change to this module (Slice B →
# ``UserInvitation``; Slice E → ``PurchaseOrder`` / ``MaterialVariant``).
#
# Resolver contract (owned by N1; rendered by N2):
#     resolver(source_id: str) -> dict
#   - registered type + object exists -> a dict of display fields (the exact keys
#     are documented on each resolver below).
#   - unregistered source_type, NULL source_type, or a source object that no
#     longer exists -> ``{}`` (an empty dict, never ``None``).

_SOURCE_RESOLVERS: dict[str, Callable[[str], dict]] = {}


def register_source(source_type: str, resolver: Callable[[str], dict]) -> None:
    """Register a Linked Context resolver for a ``source_type`` (a model name)."""
    _SOURCE_RESOLVERS[source_type] = resolver


def linked_context(source_type: str | None, source_id: str | None) -> dict:
    """Live summary of a notification's source object, or ``{}``.

    Empty for a NULL/unregistered ``source_type`` or a source object that no
    longer exists — the Linked Context panel then shows nothing.
    """
    if not source_type:
        return {}
    resolver = _SOURCE_RESOLVERS.get(source_type)
    if resolver is None:
        return {}
    return resolver(source_id)


def _change_request_context(source_id: str) -> dict:
    """Linked Context for a ChangeRequest source.

    Keys: ``code``, ``field``, ``current``, ``proposed``, ``status``.
    """
    cr = ChangeRequest.objects.filter(pk=source_id).first()
    if cr is None:
        return {}
    return {
        "code": cr.code,
        "field": cr.target_field,
        "current": cr.current_value,
        "proposed": cr.proposed_value,
        "status": cr.get_status_display(),
    }


def _user_context(source_id: str) -> dict:
    """Linked Context for a User source.

    Keys: ``name``, ``email``, ``role``, ``status``.
    """
    user = User.objects.filter(pk=source_id).first()
    if user is None:
        return {}
    return {
        "name": user.get_full_name() or user.email,
        "email": user.email,
        "role": user.get_account_role_display(),
        "status": user.get_status_display(),
    }


register_source("ChangeRequest", _change_request_context)
register_source("User", _user_context)
