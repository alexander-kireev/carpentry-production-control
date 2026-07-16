"""ChangeRequest model (D1).

The workshop-scoped REQ-### business id (two workshops both start at REQ-001),
the one-pending-CR-per-requester partial-unique constraint (allow → block →
allow-after-resolve, with synthetic users), the (workshop, code) uniqueness,
cancel_reason causes, field defaults, and factory / trait validity — all against
PostgreSQL. Model only; services / UI / notifications are D3 / N3.
"""

import re

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import ChangeRequest
from tests.factories import ChangeRequestFactory, UserFactory, WorkshopFactory

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Factory validity — pending steady state + resolved traits
# --------------------------------------------------------------------------- #


def test_factory_produces_valid_pending_user_target_cr():
    cr = ChangeRequestFactory()
    assert cr.pk is not None
    assert cr.status == ChangeRequest.Status.PENDING == "pending"
    assert cr.target_type == ChangeRequest.TargetType.USER == "user"
    assert cr.cancel_reason is None
    assert cr.resolved_at is None
    # User-target CR collapses onto the requester (MVP): target_id == requested_by_id.
    assert cr.target_id == cr.requested_by_id


@pytest.mark.parametrize("trait", ["approved", "rejected", "cancelled"])
def test_factory_resolved_traits_are_valid(trait):
    cr = ChangeRequestFactory(**{trait: True})
    assert cr.pk is not None
    assert cr.status == trait
    assert cr.resolved_at is not None


def test_cancelled_trait_defaults_to_superseded_cause():
    cr = ChangeRequestFactory(cancelled=True)
    assert cr.status == ChangeRequest.Status.CANCELLED
    assert cr.cancel_reason == ChangeRequest.CancelReason.SUPERSEDED


# --------------------------------------------------------------------------- #
# Fields / defaults
# --------------------------------------------------------------------------- #


def test_status_defaults_to_pending():
    # Model-level default on an unsaved instance (no explicit status supplied).
    assert ChangeRequest().status == ChangeRequest.Status.PENDING == "pending"


def test_target_type_offers_only_the_user_choice():
    # MVP ships the user target only; no dormant/dead choices (D-124).
    values = [value for value, _label in ChangeRequest._meta.get_field("target_type").choices]
    assert values == ["user"]


def test_cancel_reason_is_nullable_and_none_by_default():
    assert ChangeRequest._meta.get_field("cancel_reason").null is True
    assert ChangeRequestFactory().cancel_reason is None


# --------------------------------------------------------------------------- #
# REQ-### business id — per-workshop sequence (mirrors Station ST-NNN)
# --------------------------------------------------------------------------- #


def test_code_assigned_on_create():
    cr = ChangeRequestFactory()
    assert re.fullmatch(r"REQ-\d{3}", cr.code)


def test_codes_increment_within_workshop():
    workshop = WorkshopFactory()
    first = ChangeRequestFactory(workshop=workshop)
    second = ChangeRequestFactory(workshop=workshop)
    assert first.code == "REQ-001"
    assert second.code == "REQ-002"


def test_two_workshops_each_start_at_req_001():
    first = ChangeRequestFactory()  # own workshop
    second = ChangeRequestFactory()  # different workshop
    assert first.code == "REQ-001"
    assert second.code == "REQ-001"


def test_code_stable_on_update():
    cr = ChangeRequestFactory()
    original = cr.code
    cr.reason = "Updated reason."
    cr.save()
    cr.refresh_from_db()
    assert cr.code == original


def test_workshop_code_unique():
    # (workshop, code) is unique; forcing a duplicate explicit code is rejected.
    workshop = WorkshopFactory()
    ChangeRequestFactory(workshop=workshop, code="REQ-001")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ChangeRequestFactory(workshop=workshop, code="REQ-001")


# --------------------------------------------------------------------------- #
# One-pending-CR-per-requester partial-unique constraint (KI-009 DB backstop)
# --------------------------------------------------------------------------- #


def test_second_pending_cr_for_same_requester_is_blocked():
    # Synthetic user; the CR and its requester share one workshop (tenancy-consistent).
    user = UserFactory()
    ChangeRequestFactory(requested_by=user, workshop=user.workshop)  # first pending: allowed
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ChangeRequestFactory(requested_by=user, workshop=user.workshop)


@pytest.mark.parametrize(
    "resolved_status",
    [
        ChangeRequest.Status.APPROVED,
        ChangeRequest.Status.REJECTED,
        ChangeRequest.Status.CANCELLED,
    ],
)
def test_second_cr_allowed_once_first_resolved(resolved_status):
    user = UserFactory()
    first = ChangeRequestFactory(requested_by=user, workshop=user.workshop)
    # Resolving the first releases the pending slot (the constraint is status=pending only).
    first.status = resolved_status
    first.resolved_at = timezone.now()
    first.save()
    second = ChangeRequestFactory(requested_by=user, workshop=user.workshop)
    assert second.pk is not None
    assert ChangeRequest.objects.filter(requested_by=user).count() == 2


def test_different_requesters_may_each_have_a_pending_cr():
    # The guard is per-requester, not per-workshop: two users, both pending, is fine.
    workshop = WorkshopFactory()
    user_one = UserFactory(workshop=workshop)
    user_two = UserFactory(workshop=workshop)
    ChangeRequestFactory(requested_by=user_one, workshop=workshop)
    ChangeRequestFactory(requested_by=user_two, workshop=workshop)
    assert ChangeRequest.objects.filter(status=ChangeRequest.Status.PENDING).count() == 2


# --------------------------------------------------------------------------- #
# cancel_reason — accepts either cause on a cancelled CR
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cause",
    [
        ChangeRequest.CancelReason.SUPERSEDED,
        ChangeRequest.CancelReason.REQUESTER_DEACTIVATED,
    ],
)
def test_cancelled_cr_accepts_either_cause(cause):
    cr = ChangeRequestFactory(cancelled=True, cancel_reason=cause)
    cr.refresh_from_db()
    assert cr.status == ChangeRequest.Status.CANCELLED
    assert cr.cancel_reason == cause
