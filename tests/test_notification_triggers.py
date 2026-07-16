"""Live Slice-D notification triggers (N3).

The three triggers N3 wires into ``accounts.services``: an approved identity
ChangeRequest and an admin's own-profile identity edit both fan out the
``account`` notification (name → affected user + active managers; DOB → affected
user only), and a rejected CR pushes ``cr_rejected`` to the requester. Covers the
name-vs-DOB recipient split, actor-silence for both the approving admin and the
self-editing admin, dedup when the requester is a manager, the zero-recipient
self-DOB no-op, the resolution note on rejection, correct source pointers (so
N2's Linked Context resolves), and recipient visibility on ``/notifications`` +
the nav badge.

Runs against PostgreSQL (config.settings.test) — never SQLite.
"""

from datetime import date

import pytest

from accounts import services
from notifications.models import Notification
from notifications.services import linked_context
from tests.factories import UserFactory, WorkshopFactory

pytestmark = pytest.mark.django_db


def make_workshop(*, managers=1, **operator_kwargs):
    """A workshop with its admin, one operator, and ``managers`` active managers.

    Managers are created ``active`` because ``active_managers`` (the name-change
    recipient set) filters on ``status=active`` — a pending manager is not a
    recipient. Returns the manager list so a caller can vary the count.
    """
    workshop = WorkshopFactory()
    admin = UserFactory(account_role="admin", status="active", workshop=workshop)
    operator = UserFactory(
        account_role="operator", status="active", workshop=workshop, **operator_kwargs
    )
    manager_users = [
        UserFactory(account_role="manager", status="active", workshop=workshop)
        for _ in range(managers)
    ]
    return workshop, admin, operator, manager_users


def _recipient_ids(queryset=None):
    qs = queryset if queryset is not None else Notification.objects.all()
    return set(qs.values_list("recipient", flat=True))


# --- CR approved → account (name: requester + active managers) ------------


def test_approve_name_cr_notifies_requester_and_active_managers_not_admin():
    workshop, admin, operator, managers = make_workshop(managers=1, first_name="Alex")
    manager = managers[0]
    cr = services.submit_cr(operator, "first_name", "Alexander", "Legal name change.")
    services.approve_cr(cr, admin, note="Verified.")

    account = Notification.objects.filter(category=Notification.Category.ACCOUNT)
    assert account.count() == 2  # one each, no duplicates
    assert _recipient_ids(account) == {operator.pk, manager.pk}
    assert admin.pk not in _recipient_ids(account)  # approver actor-silenced

    # source = the affected user (base target = their own Profile).
    notification = account.first()
    assert notification.source_type == "User"
    assert notification.source_id == str(operator.pk)


def test_approve_name_cr_only_notifies_active_managers():
    workshop, admin, operator, managers = make_workshop(managers=2)
    inactive = UserFactory(account_role="manager", status="inactive", workshop=workshop)
    cr = services.submit_cr(operator, "last_name", "Turner", "reason")
    services.approve_cr(cr, admin)

    recipients = _recipient_ids()
    assert recipients == {operator.pk, managers[0].pk, managers[1].pk}
    assert inactive.pk not in recipients


def test_approve_name_cr_dedups_when_requester_is_a_manager():
    workshop = WorkshopFactory()
    admin = UserFactory(account_role="admin", status="active", workshop=workshop)
    requester = UserFactory(account_role="manager", status="active", workshop=workshop)
    other_manager = UserFactory(account_role="manager", status="active", workshop=workshop)

    cr = services.submit_cr(requester, "first_name", "Alexander", "reason")
    services.approve_cr(cr, admin)

    # The requester is both the affected user and an active manager — one record.
    assert Notification.objects.filter(recipient=requester).count() == 1
    assert _recipient_ids() == {requester.pk, other_manager.pk}
    assert admin.pk not in _recipient_ids()


# --- CR approved → account (DOB: requester only) --------------------------


def test_approve_dob_cr_notifies_requester_only():
    workshop, admin, operator, managers = make_workshop(managers=2)
    cr = services.submit_cr(operator, "date_of_birth", date(1985, 6, 15), "Correction.")
    services.approve_cr(cr, admin)

    assert _recipient_ids() == {operator.pk}
    for manager in managers:
        assert manager.pk not in _recipient_ids()

    notification = Notification.objects.get()
    assert notification.category == Notification.Category.ACCOUNT
    assert notification.source_type == "User"
    assert notification.source_id == str(operator.pk)


# --- CR rejected → cr_rejected (requester only, carries the note) ---------


def test_reject_cr_notifies_requester_with_note_only():
    workshop, admin, operator, managers = make_workshop(managers=1)
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.reject_cr(cr, admin, "Please raise this with your manager first.")

    notifications = Notification.objects.all()
    assert notifications.count() == 1
    notification = notifications.get()
    assert notification.recipient_id == operator.pk
    assert notification.category == Notification.Category.CR_REJECTED
    assert notification.body == "Please raise this with your manager first."
    assert cr.code in notification.title
    # source = the ChangeRequest (base target = the requester's tracking surface).
    assert notification.source_type == "ChangeRequest"
    assert notification.source_id == str(cr.pk)
    # Neither the managers nor the rejecting admin are notified.
    assert managers[0].pk not in _recipient_ids(notifications)
    assert admin.pk not in _recipient_ids(notifications)


# --- Admin own-profile self-edit → account --------------------------------


def test_admin_self_edit_name_notifies_managers_only():
    workshop, admin, operator, managers = make_workshop(managers=1)
    manager = managers[0]
    services.apply_identity_change(admin, "first_name", "Samuel", "Prefer full name.", admin)

    # Actor-silence removes the admin (the affected user here), leaving managers.
    assert _recipient_ids() == {manager.pk}
    assert admin.pk not in _recipient_ids()

    notification = Notification.objects.get()
    assert notification.category == Notification.Category.ACCOUNT
    assert notification.source_type == "User"
    assert notification.source_id == str(admin.pk)


def test_admin_self_edit_dob_notifies_no_one():
    # U-2b (affected user only) + actor-silence ⇒ the sole recipient is the actor,
    # so the fan-out is empty. It still fires — a zero-recipient no-op is valid,
    # asserted explicitly so it is not mistaken for a missing notification.
    workshop, admin, operator, managers = make_workshop(managers=1)
    services.apply_identity_change(
        admin, "date_of_birth", date(1980, 2, 2), "Correction.", admin
    )
    assert Notification.objects.count() == 0


# --- The reuse seam: apply_identity_change with actor != target -----------


def test_apply_identity_change_cross_user_notifies_target_and_managers():
    # The forward-provision for Slice B's Edit User panel (actor != target): an
    # admin directly edits the operator's name. The operator + active managers are
    # notified; the editing admin is actor-silenced — Slice B inherits this by
    # calling apply_identity_change, with no notification code of its own.
    workshop, admin, operator, managers = make_workshop(managers=1)
    manager = managers[0]
    services.apply_identity_change(operator, "first_name", "Al", "Admin correction.", admin)

    assert _recipient_ids() == {operator.pk, manager.pk}
    assert admin.pk not in _recipient_ids()


def test_apply_identity_change_cross_user_dob_notifies_target_only():
    workshop, admin, operator, managers = make_workshop(managers=1)
    services.apply_identity_change(
        operator, "date_of_birth", date(1975, 3, 3), "Admin correction.", admin
    )
    assert _recipient_ids() == {operator.pk}


# --- Zero active managers (KI-013, accepted) ------------------------------


def test_approve_name_cr_with_no_active_managers_still_fires():
    workshop, admin, operator, _ = make_workshop(managers=0)
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.approve_cr(cr, admin)
    # Fewer recipients, not a skipped call: the requester still gets their record.
    assert _recipient_ids() == {operator.pk}


# --- Linked Context resolves from the source pointer ----------------------


def test_account_notification_source_resolves_linked_context():
    workshop, admin, operator, _ = make_workshop(managers=0, first_name="Alex")
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.approve_cr(cr, admin)

    notification = Notification.objects.get()
    context = linked_context(notification.source_type, notification.source_id)
    assert context  # non-empty: the User registry resolved it
    assert context["email"] == operator.email


def test_cr_rejected_notification_source_resolves_linked_context():
    workshop, admin, operator, _ = make_workshop(managers=0)
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.reject_cr(cr, admin, "No.")

    notification = Notification.objects.get()
    context = linked_context(notification.source_type, notification.source_id)
    assert context["code"] == cr.code


# --- Recipient visibility on /notifications + the nav badge ---------------


def test_recipient_sees_account_notification_on_page_and_badge(client):
    workshop, admin, operator, managers = make_workshop(managers=1, first_name="Alex")
    manager = managers[0]
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.approve_cr(cr, admin, note="ok")

    client.force_login(manager)
    page = client.get("/notifications").content.decode()
    assert "First name updated for" in page

    # The manager's one unread lands on their nav badge.
    landing = client.get("/manager").content.decode()
    assert 'notif-nav-badge">1</span>' in landing
