"""Notification model, state machine, and services (N1).

Fields/defaults; the unread<->read / ->dismissed state machine and the
dismissed-terminal guard; pinned/important flag independence (in any state);
``notify()`` fan-out + dedup + source derivation; ``active_managers()`` scoping;
the Linked Context source registry (registered / NULL / unregistered / missing);
factory + traits — all against PostgreSQL. Mechanism only; no triggers are wired
(that is N3).
"""

import pytest

from accounts.models import User
from notifications import services as notification_services
from notifications.models import Notification
from notifications.services import (
    active_managers,
    linked_context,
    notify,
)
from tests.factories import (
    ChangeRequestFactory,
    NotificationFactory,
    UserFactory,
    WorkshopFactory,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fields / defaults + factory
# --------------------------------------------------------------------------- #


def test_factory_produces_unread_notification():
    note = NotificationFactory()
    assert note.pk is not None
    assert note.status == Notification.Status.UNREAD == "unread"
    assert note.pinned is False
    assert note.important is False
    assert note.body is None
    assert note.source_type is None
    assert note.source_id is None
    assert note.created_at is not None


def test_status_defaults_to_unread_on_unsaved_instance():
    assert Notification().status == Notification.Status.UNREAD == "unread"


@pytest.mark.parametrize(
    "trait, expected",
    [
        ("read", Notification.Status.READ),
        ("dismissed", Notification.Status.DISMISSED),
    ],
)
def test_factory_status_traits(trait, expected):
    note = NotificationFactory(**{trait: True})
    assert note.status == expected


def test_no_workshop_field():
    # Scoped transitively via recipient — there is deliberately no workshop FK.
    field_names = {f.name for f in Notification._meta.get_fields()}
    assert "workshop" not in field_names


def test_full_category_enum_present():
    # D-127 fixes the whole enum now so B/E add none. Spot-check one value per
    # cluster and assert the full count, so a dropped value fails loudly.
    values = set(Notification.Category.values)
    for expected in [
        "clearance_changed",
        "account",
        "clr_cancelled",
        "cr_rejected",
        "station_breakdown",
        "op_assigned",
        "leave_submitted",
        "order_ready",
        "work_item_assigned",
        "stock_out",
        "po_arrived",
        "report_raised",
        "invite_accepted",
        "message",
    ]:
        assert expected in values
    assert len(values) == 48


# --------------------------------------------------------------------------- #
# State machine — unread <-> read; unread/read -> dismissed; dismissed terminal
# --------------------------------------------------------------------------- #


def test_mark_read_moves_unread_to_read():
    note = NotificationFactory()
    note.mark_read()
    note.refresh_from_db()
    assert note.status == Notification.Status.READ


def test_mark_unread_moves_read_to_unread():
    note = NotificationFactory(read=True)
    note.mark_unread()
    note.refresh_from_db()
    assert note.status == Notification.Status.UNREAD


def test_dismiss_from_unread():
    note = NotificationFactory()
    note.dismiss()
    note.refresh_from_db()
    assert note.status == Notification.Status.DISMISSED


def test_dismiss_from_read():
    note = NotificationFactory(read=True)
    note.dismiss()
    note.refresh_from_db()
    assert note.status == Notification.Status.DISMISSED


def test_mark_read_is_idempotent():
    note = NotificationFactory(read=True)
    note.mark_read()  # already read: no error, no change
    note.refresh_from_db()
    assert note.status == Notification.Status.READ


@pytest.mark.parametrize("method", ["mark_read", "mark_unread"])
def test_dismissed_is_terminal(method):
    note = NotificationFactory(dismissed=True)
    with pytest.raises(ValueError):
        getattr(note, method)()
    note.refresh_from_db()
    assert note.status == Notification.Status.DISMISSED


def test_dismiss_is_idempotent():
    note = NotificationFactory(dismissed=True)
    note.dismiss()  # already dismissed: no error, no change
    note.refresh_from_db()
    assert note.status == Notification.Status.DISMISSED


# --------------------------------------------------------------------------- #
# Personal flags — independent of status, valid in any state
# --------------------------------------------------------------------------- #


def test_set_pinned_does_not_change_status():
    note = NotificationFactory()
    note.set_pinned(True)
    note.refresh_from_db()
    assert note.pinned is True
    assert note.status == Notification.Status.UNREAD


def test_set_important_does_not_change_status():
    note = NotificationFactory(read=True)
    note.set_important(True)
    note.refresh_from_db()
    assert note.important is True
    assert note.status == Notification.Status.READ


def test_flags_toggle_on_a_dismissed_notification():
    # A dismissed record is terminal for status, but flags still toggle freely.
    note = NotificationFactory(dismissed=True)
    note.set_pinned(True)
    note.set_important(True)
    note.refresh_from_db()
    assert note.pinned is True
    assert note.important is True
    assert note.status == Notification.Status.DISMISSED


# --------------------------------------------------------------------------- #
# notify() — fan-out, dedup, source derivation
# --------------------------------------------------------------------------- #


def test_notify_creates_one_record_per_recipient():
    workshop = WorkshopFactory()
    u1 = UserFactory(workshop=workshop)
    u2 = UserFactory(workshop=workshop)
    created = notify(
        [u1, u2],
        category=Notification.Category.ACCOUNT,
        title="Your profile changed",
        body="Name updated.",
    )
    assert len(created) == 2
    assert {n.recipient_id for n in created} == {u1.id, u2.id}
    for n in created:
        assert n.category == Notification.Category.ACCOUNT
        assert n.title == "Your profile changed"
        assert n.body == "Name updated."
        assert n.status == Notification.Status.UNREAD


def test_notify_dedups_recipients():
    user = UserFactory()
    created = notify(
        [user, user, user],
        category=Notification.Category.ACCOUNT,
        title="One only",
    )
    assert len(created) == 1
    assert Notification.objects.filter(recipient=user).count() == 1


def test_notify_body_defaults_to_none():
    user = UserFactory()
    (note,) = notify([user], category=Notification.Category.ACCOUNT, title="No body")
    assert note.body is None


def test_notify_derives_source_pointer_from_instance():
    cr = ChangeRequestFactory()
    (note,) = notify(
        [cr.requested_by],
        category=Notification.Category.CR_REJECTED,
        title="Change request rejected",
        source=cr,
    )
    assert note.source_type == "ChangeRequest"
    assert note.source_id == str(cr.pk)


def test_notify_without_source_leaves_pointer_null():
    user = UserFactory()
    (note,) = notify([user], category=Notification.Category.ACCOUNT, title="No source")
    assert note.source_type is None
    assert note.source_id is None


def test_notify_returns_empty_for_no_recipients():
    assert notify([], category=Notification.Category.ACCOUNT, title="none") == []


# --------------------------------------------------------------------------- #
# active_managers() — workshop + role + status scoped
# --------------------------------------------------------------------------- #


def test_active_managers_scoped_to_workshop_role_and_status():
    ws = WorkshopFactory()
    other = WorkshopFactory()
    mgr = UserFactory(
        workshop=ws,
        account_role=User.AccountRole.MANAGER,
        status=User.Status.ACTIVE,
    )
    # Excluded: inactive manager, active non-manager, manager in another workshop.
    UserFactory(
        workshop=ws,
        account_role=User.AccountRole.MANAGER,
        status=User.Status.INACTIVE,
    )
    UserFactory(
        workshop=ws,
        account_role=User.AccountRole.OPERATOR,
        status=User.Status.ACTIVE,
    )
    UserFactory(
        workshop=other,
        account_role=User.AccountRole.MANAGER,
        status=User.Status.ACTIVE,
    )
    assert list(active_managers(ws)) == [mgr]


# --------------------------------------------------------------------------- #
# Linked Context registry — registered / NULL / unregistered / missing
# --------------------------------------------------------------------------- #


def test_linked_context_resolves_registered_change_request():
    cr = ChangeRequestFactory()
    ctx = linked_context("ChangeRequest", str(cr.pk))
    assert ctx == {
        "code": cr.code,
        "field": cr.target_field,
        "current": cr.current_value,
        "proposed": cr.proposed_value,
        "status": cr.get_status_display(),
    }


def test_linked_context_resolves_registered_user():
    user = UserFactory(first_name="Ada", last_name="Lovelace")
    ctx = linked_context("User", str(user.pk))
    assert ctx == {
        "name": "Ada Lovelace",
        "email": user.email,
        "role": user.get_account_role_display(),
        "status": user.get_status_display(),
    }


def test_linked_context_empty_for_null_source_type():
    assert linked_context(None, None) == {}


def test_linked_context_empty_for_unregistered_source_type():
    assert linked_context("Widget", "1") == {}


def test_linked_context_empty_when_source_object_missing():
    # Registered type, but the object was deleted since — resolve to empty, not error.
    assert linked_context("User", "99999999") == {}


def test_register_source_extends_the_registry():
    # B/E extend the registry with no change to notifications.services.
    notification_services.register_source(
        "Gadget", lambda source_id: {"gadget_id": source_id}
    )
    try:
        assert linked_context("Gadget", "7") == {"gadget_id": "7"}
    finally:
        notification_services._SOURCE_RESOLVERS.pop("Gadget", None)
