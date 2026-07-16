"""Identity ChangeRequest workflow (Slice D / D3) — services and HTTP surface.

Covers submission, the one-pending guard (friendly message + DB constraint
backstop + release), auto-apply on approval, rejection (mandatory reason +
reason surfaced), the admin own-profile direct edit, the ``apply_identity_change``
supersede branch (the Slice B cross-user path, exercised at the service level
here), permission denials, and cross-workshop isolation. The notifications these
services now fan out (N3) are covered in ``test_notification_triggers.py``.

Runs against PostgreSQL (config.settings.test) — never SQLite; the partial
unique constraint and its IntegrityError backstop are Postgres-level behaviour.
"""

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from accounts import services
from accounts.models import ChangeRequest
from tests.factories import ChangeRequestFactory, UserFactory, WorkshopFactory

pytestmark = pytest.mark.django_db


def make_pair(**operator_kwargs):
    """A workshop with its admin and one operator, all in the same workshop."""
    workshop = WorkshopFactory()
    admin = UserFactory(account_role="admin", workshop=workshop)
    operator = UserFactory(account_role="operator", workshop=workshop, **operator_kwargs)
    return workshop, admin, operator


# --- submit_cr ------------------------------------------------------------


def test_submit_cr_creates_pending_cr_assigned_to_admin():
    workshop, admin, operator = make_pair(first_name="Alex")
    cr = services.submit_cr(operator, "first_name", "Alexander", "Legal name change.")

    assert cr.status == ChangeRequest.Status.PENDING
    assert cr.assigned_to == admin
    assert cr.requested_by == operator
    assert cr.workshop == workshop
    assert cr.target_type == ChangeRequest.TargetType.USER
    assert cr.target_id == operator.id
    assert cr.current_value == "Alex"
    assert cr.proposed_value == "Alexander"
    assert cr.code.startswith("REQ-")
    # The profile field itself is untouched until approval.
    operator.refresh_from_db()
    assert operator.first_name == "Alex"


def test_submit_cr_serializes_date_of_birth():
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "date_of_birth", date(1985, 6, 15), "Correction.")
    assert cr.proposed_value == "1985-06-15"


def test_submit_cr_rejects_admin_actor():
    workshop, admin, operator = make_pair()
    with pytest.raises(ValueError):
        services.submit_cr(admin, "first_name", "Bob", "reason")


def test_submit_cr_rejects_unknown_field():
    workshop, admin, operator = make_pair()
    with pytest.raises(ValueError):
        services.submit_cr(operator, "email", "x@example.com", "reason")


def test_submit_cr_without_workshop_admin_degrades_gracefully():
    # A missing admin is a broken invariant — degrade to a domain error the view
    # can message, rather than a 500 from a bare .get().
    workshop = WorkshopFactory()
    operator = UserFactory(account_role="operator", workshop=workshop)
    with pytest.raises(services.WorkshopHasNoAdminError):
        services.submit_cr(operator, "first_name", "X", "reason")


# --- one-pending guard ----------------------------------------------------


def test_one_pending_guard_blocks_second_submission_with_message():
    workshop, admin, operator = make_pair()
    first = services.submit_cr(operator, "first_name", "A", "reason")
    with pytest.raises(services.PendingChangeRequestError) as exc:
        services.submit_cr(operator, "last_name", "B", "reason")
    # The friendly message names the already-open request.
    assert first.code in str(exc.value)


def test_pending_partial_unique_constraint_is_the_race_backstop():
    # Bypass the friendly pre-check to prove the DB constraint independently
    # rejects a second pending CR for the same requester (the concurrency floor).
    workshop, admin, operator = make_pair()
    ChangeRequestFactory(
        workshop=workshop, requested_by=operator, assigned_to=admin, target_id=operator.id
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ChangeRequestFactory(
                workshop=workshop,
                requested_by=operator,
                assigned_to=admin,
                target_id=operator.id,
            )


def test_guard_releases_once_the_pending_cr_is_resolved():
    workshop, admin, operator = make_pair()
    first = services.submit_cr(operator, "first_name", "A", "reason")
    services.reject_cr(first, admin, "Not now.")
    # A new submission is allowed once the first is resolved.
    second = services.submit_cr(operator, "last_name", "B", "reason")
    assert second.status == ChangeRequest.Status.PENDING


# --- approve_cr (auto-apply) ---------------------------------------------


def test_approve_cr_auto_applies_name_and_marks_approved():
    workshop, admin, operator = make_pair(first_name="Alex")
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.approve_cr(cr, admin, note="Verified.")

    cr.refresh_from_db()
    operator.refresh_from_db()
    assert cr.status == ChangeRequest.Status.APPROVED
    assert cr.resolved_at is not None
    assert cr.resolution_note == "Verified."
    assert operator.first_name == "Alexander"


def test_approve_cr_auto_applies_date_of_birth():
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "date_of_birth", date(1985, 6, 15), "reason")
    services.approve_cr(cr, admin)
    operator.refresh_from_db()
    assert operator.date_of_birth == date(1985, 6, 15)


def test_approve_cr_rejects_already_resolved_cr():
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.approve_cr(cr, admin)
    with pytest.raises(services.ChangeRequestNotPendingError):
        services.approve_cr(cr, admin)


# --- reject_cr ------------------------------------------------------------


def test_reject_cr_requires_a_reason():
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    with pytest.raises(ValueError):
        services.reject_cr(cr, admin, "   ")
    cr.refresh_from_db()
    assert cr.status == ChangeRequest.Status.PENDING


def test_reject_cr_records_reason_and_applies_nothing():
    workshop, admin, operator = make_pair(first_name="Alex")
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.reject_cr(cr, admin, "Raise this with your manager first.")

    cr.refresh_from_db()
    operator.refresh_from_db()
    assert cr.status == ChangeRequest.Status.REJECTED
    assert cr.resolution_note == "Raise this with your manager first."
    assert cr.resolved_at is not None
    assert operator.first_name == "Alex"  # unchanged


# --- apply_identity_change (direct edit + supersede) ----------------------


def test_apply_identity_change_writes_directly_without_a_cr():
    workshop, admin, operator = make_pair()
    admin.first_name = "Sam"
    admin.save()
    services.apply_identity_change(admin, "first_name", "Samuel", "Prefer full name.", admin)
    admin.refresh_from_db()
    assert admin.first_name == "Samuel"
    assert ChangeRequest.objects.count() == 0


def test_apply_identity_change_requires_a_reason():
    workshop, admin, operator = make_pair()
    with pytest.raises(ValueError):
        services.apply_identity_change(admin, "first_name", "Samuel", "  ", admin)


def test_apply_identity_change_supersedes_a_matching_pending_cr():
    # The Slice B cross-user path (actor != target): an admin directly edits the
    # operator's field while the operator's CR for that field is pending.
    workshop, admin, operator = make_pair(first_name="Alex")
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    services.apply_identity_change(operator, "first_name", "Al", "Admin correction.", admin)

    cr.refresh_from_db()
    operator.refresh_from_db()
    assert operator.first_name == "Al"
    assert cr.status == ChangeRequest.Status.CANCELLED
    assert cr.cancel_reason == ChangeRequest.CancelReason.SUPERSEDED
    assert cr.resolved_at is not None


def test_apply_identity_change_only_supersedes_the_same_field():
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "last_name", "Novak", "reason")
    services.apply_identity_change(operator, "first_name", "Al", "reason", admin)
    cr.refresh_from_db()
    assert cr.status == ChangeRequest.Status.PENDING


# --- describe_change_request (tracking-surface view-model) -----------------


def test_superseded_cr_renders_distinctly():
    cr = ChangeRequestFactory(cancelled=True, target_field="first_name")
    view_model = services.describe_change_request(cr)
    assert view_model["status_label"] == "Superseded"
    assert view_model["is_superseded"] is True
    assert "updated directly by admin" in view_model["detail"]


def test_rejected_cr_surfaces_its_note():
    cr = ChangeRequestFactory(rejected=True)
    view_model = services.describe_change_request(cr)
    assert view_model["status_label"] == "Rejected"
    assert view_model["detail"] == cr.resolution_note


# --- HTTP: submission, queue, tracking ------------------------------------


def test_submit_via_view_appears_in_queue_and_tracking(client):
    workshop, admin, operator = make_pair(first_name="Alex")
    client.force_login(operator)
    response = client.post(
        "/change-requests/submit",
        {
            "target_field": "first_name",
            "first_name-proposed_value": "Alexander",
            "first_name-reason": "Legal name change.",
        },
    )
    assert response.status_code == 302
    cr = ChangeRequest.objects.get()
    assert cr.requested_by == operator
    assert cr.proposed_value == "Alexander"

    # Admin CR-only queue lists it.
    client.force_login(admin)
    queue = client.get("/admin/requests").content.decode()
    assert cr.code in queue
    assert "Alexander" in queue

    # The operator's own "Your requests" surface lists it.
    client.force_login(operator)
    tracking = client.get("/operator/requests").content.decode()
    assert cr.code in tracking
    assert "Alexander" in tracking

    # The profile still shows the old value + the pending-request note.
    profile = client.get("/profile").content.decode()
    assert cr.code in profile  # the one-pending note names it
    assert "Alex" in profile


def test_manager_tracking_lists_own_requests(client):
    workshop = WorkshopFactory()
    UserFactory(account_role="admin", workshop=workshop)
    manager = UserFactory(account_role="manager", workshop=workshop)
    cr = services.submit_cr(manager, "last_name", "Fisher", "Marriage.")
    client.force_login(manager)
    content = client.get("/manager/my-work").content.decode()
    assert cr.code in content
    assert "Fisher" in content


def test_approve_via_view_applies_change(client):
    workshop, admin, operator = make_pair(first_name="Alex")
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    client.force_login(admin)
    response = client.post(f"/admin/requests/{cr.pk}/approve")
    assert response.status_code == 302
    cr.refresh_from_db()
    operator.refresh_from_db()
    assert cr.status == ChangeRequest.Status.APPROVED
    assert operator.first_name == "Alexander"


def test_reject_via_view_needs_reason_then_surfaces_it(client):
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    client.force_login(admin)

    # No note → stays pending.
    client.post(f"/admin/requests/{cr.pk}/reject", {"note": ""})
    cr.refresh_from_db()
    assert cr.status == ChangeRequest.Status.PENDING

    # With a note → rejected, reason recorded.
    client.post(f"/admin/requests/{cr.pk}/reject", {"note": "Talk to your manager."})
    cr.refresh_from_db()
    assert cr.status == ChangeRequest.Status.REJECTED

    # The requester sees the reason on their tracking surface.
    client.force_login(operator)
    tracking = client.get("/operator/requests").content.decode()
    assert "Talk to your manager." in tracking


def test_admin_own_profile_edit_via_view(client):
    workshop, admin, operator = make_pair()
    admin.first_name = "Sam"
    admin.save()
    client.force_login(admin)
    response = client.post(
        "/change-requests/identity",
        {
            "first_name": "Samuel",
            "last_name": admin.last_name,
            "date_of_birth": admin.date_of_birth.isoformat(),
            "reason": "Prefer my full name.",
        },
    )
    assert response.status_code == 302
    admin.refresh_from_db()
    assert admin.first_name == "Samuel"
    assert ChangeRequest.objects.count() == 0


def test_admin_identity_edit_requires_reason(client):
    workshop, admin, operator = make_pair()
    admin.first_name = "Sam"
    admin.save()
    client.force_login(admin)
    client.post(
        "/change-requests/identity",
        {
            "first_name": "Samuel",
            "last_name": admin.last_name,
            "date_of_birth": admin.date_of_birth.isoformat(),
            "reason": "",
        },
    )
    admin.refresh_from_db()
    assert admin.first_name == "Sam"  # unchanged — reason is mandatory


# --- permissions ----------------------------------------------------------


def test_non_admin_cannot_view_queue(client):
    workshop, admin, operator = make_pair()
    client.force_login(operator)
    assert client.get("/admin/requests").status_code == 403


def test_non_admin_cannot_approve(client):
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    client.force_login(operator)
    assert client.post(f"/admin/requests/{cr.pk}/approve").status_code == 403
    cr.refresh_from_db()
    assert cr.status == ChangeRequest.Status.PENDING


def test_non_admin_cannot_reject(client):
    workshop, admin, operator = make_pair()
    cr = services.submit_cr(operator, "first_name", "Alexander", "reason")
    client.force_login(operator)
    assert (
        client.post(f"/admin/requests/{cr.pk}/reject", {"note": "no"}).status_code == 403
    )
    cr.refresh_from_db()
    assert cr.status == ChangeRequest.Status.PENDING


def test_admin_cannot_submit_a_cr(client):
    workshop, admin, operator = make_pair()
    client.force_login(admin)
    response = client.post(
        "/change-requests/submit",
        {
            "target_field": "first_name",
            "first_name-proposed_value": "Bob",
            "first_name-reason": "reason",
        },
    )
    assert response.status_code == 403


# --- tenancy: workshop isolation ------------------------------------------


def test_queue_is_scoped_to_the_admins_own_workshop(client):
    _, admin_a, op_a = make_pair()
    _, _, op_b = make_pair()
    services.submit_cr(op_a, "first_name", "AlphaName", "reason")
    services.submit_cr(op_b, "first_name", "BravoName", "reason")

    client.force_login(admin_a)
    content = client.get("/admin/requests").content.decode()
    assert "AlphaName" in content
    assert "BravoName" not in content


def test_admin_cannot_act_on_another_workshops_cr(client):
    _, admin_a, _ = make_pair()
    _, _, op_b = make_pair()
    cr_b = services.submit_cr(op_b, "first_name", "BravoName", "reason")

    client.force_login(admin_a)
    assert client.post(f"/admin/requests/{cr_b.pk}/approve").status_code == 404
    cr_b.refresh_from_db()
    assert cr_b.status == ChangeRequest.Status.PENDING
