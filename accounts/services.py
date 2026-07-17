"""Accounts service layer (introduced in A1; opened up in A2 per D-126).

Business logic that shouldn't live in a view lands here, request-agnostic, so
it stays callable from a test or a management command as well as an HTTP
request. Later slices (B, D, ...) add their own functions to this module
rather than putting service logic directly in views.
"""

import datetime

from django.db import IntegrityError, transaction
from django.template.defaultfilters import date as date_filter
from django.utils import timezone

from accounts.models import ChangeRequest, User
from catalog.models import WorkshopRole
from catalog.seeds import ADMIN_ROLE_NAME
from notifications.models import Notification
from notifications.services import active_managers, notify


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


# --------------------------------------------------------------------------- #
# Identity ChangeRequest workflow (Slice D / D3)
# --------------------------------------------------------------------------- #
#
# Four request-agnostic services in the A1 tradition (no ``request``, no
# ``login``/``redirect``/session — the view translates the result into HTTP):
#
#   submit_cr             non-admin requests a change to their own identity field
#   approve_cr            admin approves; auto-applies proposed_value to the field
#   reject_cr             admin rejects; records the reason; nothing is applied
#   apply_identity_change admin writes an identity field directly, superseding a
#                         matching pending CR
#
# MVP target is the ``user`` target_type, identity fields only. ``approve_cr`` /
# ``reject_cr`` / ``apply_identity_change`` fan out notifications via N1's
# ``notify()`` (wired by N3): an approved or directly-edited identity change
# folds into the ``account`` notification (name → affected user + active
# managers; DOB → affected user only), and a rejection sends ``cr_rejected`` to
# the requester. Actor-silence (excluding the acting admin/editor) is applied
# here in the caller — ``notify()`` itself is a dumb fan-out.

# The identity fields a CR (or a direct admin edit) may target, and their
# human labels for queue/tracking rendering. The valid set is target_type-
# dependent; ``ChangeRequest.target_field`` is a generic CharField, so the
# service is the thing that validates membership.
IDENTITY_FIELDS = ("first_name", "last_name", "date_of_birth")
IDENTITY_FIELD_LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "date_of_birth": "Date of birth",
}


class ChangeRequestError(Exception):
    """Base for change-request service errors the view turns into a user message."""


class PendingChangeRequestError(ChangeRequestError):
    """A requester already has an open pending CR (one-pending-per-user guard)."""

    def __init__(self, code=None):
        self.code = code
        super().__init__(
            f"You already have a pending change request ({code}) — "
            "submit a new one once it's resolved."
            if code
            else "You already have a pending change request — "
            "submit a new one once it's resolved."
        )


class WorkshopHasNoAdminError(ChangeRequestError):
    """No admin to assign the CR to — a broken invariant, surfaced not 500'd."""

    def __init__(self):
        super().__init__(
            "This request can't be submitted right now — no workshop "
            "administrator is available to review it."
        )


class ChangeRequestNotPendingError(ChangeRequestError):
    """The CR was already resolved (e.g. a second admin acted first)."""

    def __init__(self):
        super().__init__("This change request has already been resolved.")


def serialize_identity(field: str, value) -> str:
    """Serialize an identity value for a CR's ``current_value``/``proposed_value``.

    Names store as-is; ``date_of_birth`` stores ISO (``YYYY-MM-DD``) so it round-
    trips through the CharField and re-parses cleanly on approval. A blank value
    is legitimate (e.g. an empty last name) and serializes to ``""``.
    """
    if value in (None, ""):
        return ""
    if field == "date_of_birth" and not isinstance(value, str):
        return value.isoformat()
    return str(value)


def deserialize_identity(field: str, raw: str):
    """Inverse of :func:`serialize_identity`, for writing back onto the User."""
    if field == "date_of_birth":
        return datetime.date.fromisoformat(raw)
    return raw


def _target_user(cr: ChangeRequest) -> User:
    """The user a user-target CR points at (``target_id`` collapses onto the
    requester in MVP, but resolve via the generic pointer to stay honest)."""
    return User.objects.get(pk=cr.target_id)


def submit_cr(requested_by: User, target_field: str, proposed_value, reason: str) -> ChangeRequest:
    """Create a pending identity CR for a non-admin's own field.

    Snapshots ``current_value`` from the live field, assigns the CR to the
    workshop's admin, and enforces the one-pending-per-requester guard both as a
    friendly pre-check (so the message can name the open ``REQ-###``) and via the
    D1 partial-unique constraint as the concurrency backstop. Self-submission
    only: ``target_id`` is the requester's own id.
    """
    if requested_by.account_role == User.AccountRole.ADMIN:
        # Admins edit their own identity directly (apply_identity_change); they
        # never submit a CR about themselves. Guarded in the UI too; defensive here.
        raise ValueError("Admins do not submit change requests.")
    if target_field not in IDENTITY_FIELDS:
        raise ValueError(f"{target_field!r} is not a requestable identity field.")

    # Assign to the workshop's admin. Invariant (D-126): each workshop has
    # exactly one self-registered admin owner. order_by/first (rather than get)
    # keeps a hypothetical second admin deterministic instead of raising, and a
    # missing admin degrades to a friendly message rather than a 500.
    admin = (
        User.objects.filter(
            workshop=requested_by.workshop, account_role=User.AccountRole.ADMIN
        )
        .order_by("pk")
        .first()
    )
    if admin is None:
        raise WorkshopHasNoAdminError()

    # Friendly pre-check: name the already-open request in the message.
    existing = ChangeRequest.objects.filter(
        requested_by=requested_by, status=ChangeRequest.Status.PENDING
    ).first()
    if existing is not None:
        raise PendingChangeRequestError(existing.code)

    try:
        # Own atomic block: a violated partial-unique constraint raises
        # IntegrityError, and wrapping the insert keeps that from poisoning any
        # surrounding transaction (the established idiom — catalog.services).
        with transaction.atomic():
            return ChangeRequest.objects.create(
                workshop=requested_by.workshop,
                target_type=ChangeRequest.TargetType.USER,
                target_id=requested_by.id,
                target_field=target_field,
                current_value=serialize_identity(
                    target_field, getattr(requested_by, target_field)
                ),
                proposed_value=serialize_identity(target_field, proposed_value),
                reason=reason,
                requested_by=requested_by,
                assigned_to=admin,
            )
    except IntegrityError:
        # Race backstop: another submit won between the pre-check and the insert.
        # Re-query so the message can still name the open request.
        existing = ChangeRequest.objects.filter(
            requested_by=requested_by, status=ChangeRequest.Status.PENDING
        ).first()
        raise PendingChangeRequestError(existing.code if existing else None) from None


# Name changes fan out to the workshop's managers as well as the affected user
# (U-2a); a date-of-birth change goes to the affected user alone (U-2b).
_NAME_FIELDS = ("first_name", "last_name")


def _notify_identity_change(target_user: User, field: str, *, actor: User) -> None:
    """Fan out the ``account`` notification for a name/DOB identity change.

    The shared seam for both identity-change causes: an approved CR
    (``approve_cr``) and a direct admin edit (``apply_identity_change``). A name
    change reaches the affected user **plus every active manager**; a DOB change
    reaches the affected user **only** (U-2a / U-2b). ``actor`` is always
    excluded — actor-silence is the caller's job, since ``notify()`` is a dumb
    fan-out — so an admin approving someone else's CR is never notified, and an
    admin editing their **own** identity notifies only the managers (name) or no
    one at all (DOB); a zero-recipient fan-out is a valid no-op.

    ``source`` is the affected ``User`` (base target = their own Profile). The
    title is deliberately recipient-neutral third person: the one record is
    fanned out to both the affected user and the managers, so it must read
    correctly for either audience — do not reword it to "Your …".

    Slice B's Edit User panel inherits this behaviour for free by calling
    ``apply_identity_change`` (actor ≠ target); it wires no notification code.
    """
    recipients = [target_user]
    if field in _NAME_FIELDS:
        recipients.extend(active_managers(target_user.workshop))
    recipients = [user for user in recipients if user.pk != actor.pk]

    field_label = IDENTITY_FIELD_LABELS.get(field, field)
    display_name = target_user.get_full_name() or target_user.email
    notify(
        recipients,
        category=Notification.Category.ACCOUNT,
        title=f"{field_label} updated for {display_name}",
        source=target_user,
    )


def approve_cr(cr: ChangeRequest, admin: User, note: str | None = None) -> ChangeRequest:
    """Approve a pending CR and auto-apply ``proposed_value`` to the target field.

    ``pending → approved``; the change takes effect immediately on the target
    user, with no further admin action. The applied change then folds into the
    ``account`` notification (name → requester + active managers; DOB → requester
    only), with the approving admin actor-silenced.
    """
    if cr.status != ChangeRequest.Status.PENDING:
        raise ChangeRequestNotPendingError()

    with transaction.atomic():
        target = _target_user(cr)
        setattr(target, cr.target_field, deserialize_identity(cr.target_field, cr.proposed_value))
        target.save(update_fields=[cr.target_field])

        cr.status = ChangeRequest.Status.APPROVED
        cr.resolution_note = note or None
        cr.resolved_at = timezone.now()
        cr.save(update_fields=["status", "resolution_note", "resolved_at"])

        _notify_identity_change(target, cr.target_field, actor=admin)
    return cr


def reject_cr(cr: ChangeRequest, admin: User, note: str) -> ChangeRequest:
    """Reject a pending CR. ``resolution_note`` is mandatory; nothing is applied.

    ``pending → rejected``; the reason is surfaced back to the requester on their
    tracking surface and pushed to them as a ``cr_rejected`` notification carrying
    the note. Only the requester is notified (the rejecting admin is not).
    """
    if cr.status != ChangeRequest.Status.PENDING:
        raise ChangeRequestNotPendingError()
    if not (note and note.strip()):
        raise ValueError("A reason is required to reject a change request.")

    with transaction.atomic():
        cr.status = ChangeRequest.Status.REJECTED
        cr.resolution_note = note.strip()
        cr.resolved_at = timezone.now()
        cr.save(update_fields=["status", "resolution_note", "resolved_at"])

        notify(
            [cr.requested_by],
            category=Notification.Category.CR_REJECTED,
            title=f"Change request {cr.code} was declined",
            body=cr.resolution_note,
            source=cr,
        )
    return cr


def apply_identity_change(
    target_user: User, field: str, value, reason: str, actor: User
) -> User:
    """Write an identity field directly, superseding a matching pending CR.

    A mandatory ``reason`` accompanies every direct identity edit. If a pending
    CR targets the same ``(target_user, field)``, it transitions to ``cancelled``
    with ``cancel_reason='superseded'`` so the requester's tracking surface can
    show "Superseded" distinctly from a deactivation-cancel.

    Deliberately **single-field and actor-parameterized**: D3's admin own-profile
    edit calls it with ``actor == target_user`` (which never has a CR about itself
    to supersede), and Slice B's Edit User panel reuses it verbatim with
    ``actor != target_user`` — that is the path that triggers the supersede branch
    live. Fans out the ``account`` notification for the change via
    ``_notify_identity_change`` (name → target + active managers; DOB → target),
    with ``actor`` silenced — so the admin's own-profile name edit notifies the
    managers only, and a self-edit of DOB notifies no one.
    """
    if field not in IDENTITY_FIELDS:
        raise ValueError(f"{field!r} is not an editable identity field.")
    if not (reason and reason.strip()):
        raise ValueError("A reason is required to edit an identity field.")

    with transaction.atomic():
        setattr(target_user, field, value)
        target_user.save(update_fields=[field])

        pending = ChangeRequest.objects.filter(
            target_type=ChangeRequest.TargetType.USER,
            target_id=target_user.id,
            target_field=field,
            status=ChangeRequest.Status.PENDING,
        ).first()
        if pending is not None:
            pending.status = ChangeRequest.Status.CANCELLED
            pending.cancel_reason = ChangeRequest.CancelReason.SUPERSEDED
            pending.resolved_at = timezone.now()
            pending.save(update_fields=["status", "cancel_reason", "resolved_at"])

        _notify_identity_change(target_user, field, actor=actor)
    return target_user


# --------------------------------------------------------------------------- #
# Presentation helpers — request-agnostic view-models for the CR surfaces
# --------------------------------------------------------------------------- #
#
# One place both the admin queue (accounts view) and the requester tracking
# surfaces (shell views) build their row data from, so field labels, value
# formatting, and the distinct "Superseded" status don't drift between them.


def _display_value(field: str, raw: str) -> str:
    """Human-readable form of a serialized identity value (dates → ``j M Y``)."""
    if raw in (None, ""):
        return "—"
    if field == "date_of_birth":
        try:
            return date_filter(datetime.date.fromisoformat(raw), "j M Y")
        except ValueError:
            return raw
    return raw


def describe_change_request(cr: ChangeRequest) -> dict:
    """A display view-model for one CR row.

    ``status_label`` renders a ``cancelled`` + ``superseded`` CR as the distinct
    "Superseded" (never a bare "Cancelled"), so a requester checking "what
    happened to my request?" finds the answer; ``detail`` carries the rejection
    note or the supersede explanation.
    """
    field_label = IDENTITY_FIELD_LABELS.get(cr.target_field, cr.target_field)
    is_superseded = (
        cr.status == ChangeRequest.Status.CANCELLED
        and cr.cancel_reason == ChangeRequest.CancelReason.SUPERSEDED
    )
    if is_superseded:
        status_label = "Superseded"
        detail = f"Superseded — {field_label} was updated directly by admin."
    elif cr.status == ChangeRequest.Status.REJECTED:
        status_label = cr.get_status_display()
        detail = cr.resolution_note or ""
    else:
        status_label = cr.get_status_display()
        detail = ""
    return {
        "cr": cr,
        "field_label": field_label,
        "current_display": _display_value(cr.target_field, cr.current_value),
        "proposed_display": _display_value(cr.target_field, cr.proposed_value),
        "status_label": status_label,
        "is_superseded": is_superseded,
        "detail": detail,
    }
