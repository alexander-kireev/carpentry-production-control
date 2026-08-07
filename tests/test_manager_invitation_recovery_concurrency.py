from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from events.models import Event
from identity.commands import (
    accept_permanent_manager_invitation,
    correct_workshop_timezone,
    replace_pending_permanent_manager,
    resend_permanent_manager_invitation,
)
from identity.models import EmailDeliveryIntent, User, UserInvitation
from identity.results import ResultCode
from tests.test_invitation_acceptance import PASSWORD
from tests.test_manager_invitation_recovery import (
    pending_setup,
    replacement_data,
    resend_data,
)

pytestmark = pytest.mark.django_db(transaction=True)


def race(*functions):
    barrier = Barrier(len(functions))

    def invoke(function):
        close_old_connections()
        barrier.wait()
        try:
            return function().code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        return list(pool.map(invoke, functions))


def test_acceptance_vs_resend_is_first_commit_wins(monkeypatch):
    admin, workshop, _, invitation, deliveries = pending_setup(monkeypatch)
    token = deliveries[0]["raw_token"]
    codes = race(
        lambda: accept_permanent_manager_invitation(
            selector=str(invitation.id),
            raw_token=token,
            password=PASSWORD,
            expected_generation=1,
        ),
        lambda: resend_permanent_manager_invitation(
            actor_id=admin.id,
            data=resend_data(workshop),
            idempotency_key="race-resend",
        ),
    )
    assert codes.count(ResultCode.SUCCESS) == 1
    workshop.refresh_from_db()
    invitation.refresh_from_db()
    assert (workshop.status, invitation.status) in {
        ("operational", "consumed"),
        ("manager_activation_pending", "pending"),
    }
    assert Event.objects.count() in {0, 2}


def test_acceptance_vs_replacement_is_first_commit_wins(monkeypatch):
    admin, workshop, old_candidate, invitation, deliveries = pending_setup(monkeypatch)
    token = deliveries[0]["raw_token"]
    codes = race(
        lambda: accept_permanent_manager_invitation(
            selector=str(invitation.id),
            raw_token=token,
            password=PASSWORD,
            expected_generation=1,
        ),
        lambda: replace_pending_permanent_manager(
            actor_id=admin.id,
            data=replacement_data(workshop),
            idempotency_key="race-replace",
        ),
    )
    assert codes.count(ResultCode.SUCCESS) == 1
    workshop.refresh_from_db()
    managers = list(User.objects.filter(workshop=workshop, account_role="manager"))
    assert len(managers) == 1
    if workshop.status == "operational":
        assert managers[0].id == old_candidate.id and managers[0].status == "active"
        assert Event.objects.count() == 2
    else:
        assert managers[0].id != old_candidate.id and managers[0].status == "pending"
        assert Event.objects.count() == 0


def test_resend_vs_replacement_has_one_generation_authority(monkeypatch):
    admin, workshop, _, _, _ = pending_setup(monkeypatch)
    codes = race(
        lambda: resend_permanent_manager_invitation(
            actor_id=admin.id,
            data=resend_data(workshop),
            idempotency_key="race-resend",
        ),
        lambda: replace_pending_permanent_manager(
            actor_id=admin.id,
            data=replacement_data(workshop),
            idempotency_key="race-replace",
        ),
    )
    assert codes.count(ResultCode.SUCCESS) == 1
    assert any(
        code in {ResultCode.STALE, ResultCode.INVITATION_UNAVAILABLE} for code in codes
    )
    assert User.objects.filter(account_role="manager").count() == 1
    invitation = UserInvitation.objects.get(status="pending")
    assert (
        EmailDeliveryIntent.objects.filter(
            invitation=invitation,
            invitation_generation=invitation.invitation_generation,
        ).count()
        == 1
    )


def test_duplicate_resend_advances_once(monkeypatch):
    admin, workshop, candidate, invitation, _ = pending_setup(monkeypatch)
    data = resend_data(workshop)
    codes = race(
        lambda: resend_permanent_manager_invitation(
            actor_id=admin.id, data=data, idempotency_key="duplicate-a"
        ),
        lambda: resend_permanent_manager_invitation(
            actor_id=admin.id, data=data, idempotency_key="duplicate-b"
        ),
    )
    assert codes.count(ResultCode.SUCCESS) == 1
    assert codes.count(ResultCode.STALE) == 1
    invitation.refresh_from_db()
    assert invitation.user_id == candidate.id and invitation.invitation_generation == 2


@pytest.mark.parametrize("changed_payload", (False, True))
def test_duplicate_replacement_replays_or_rejects_misuse(monkeypatch, changed_payload):
    admin, workshop, _, _, _ = pending_setup(monkeypatch)
    first = replacement_data(workshop)
    second = replacement_data(
        workshop, first_name="Changed" if changed_payload else "Riley"
    )
    codes = race(
        lambda: replace_pending_permanent_manager(
            actor_id=admin.id, data=first, idempotency_key="same-key"
        ),
        lambda: replace_pending_permanent_manager(
            actor_id=admin.id, data=second, idempotency_key="same-key"
        ),
    )
    assert codes.count(ResultCode.SUCCESS) == 1
    expected = (
        ResultCode.INVITATION_UNAVAILABLE if changed_payload else ResultCode.REPLAY
    )
    assert codes.count(expected) == 1
    assert User.objects.filter(account_role="manager").count() == 1
    assert UserInvitation.objects.count() == EmailDeliveryIntent.objects.count() == 1


def test_timezone_vs_acceptance_is_lawful_and_deadlock_free(monkeypatch):
    admin, workshop, _, invitation, deliveries = pending_setup(monkeypatch)
    token = deliveries[0]["raw_token"]
    codes = race(
        lambda: accept_permanent_manager_invitation(
            selector=str(invitation.id),
            raw_token=token,
            password=PASSWORD,
            expected_generation=1,
        ),
        lambda: correct_workshop_timezone(
            actor_id=admin.id,
            data={
                "timezone_action": "correct",
                "submission_nonce": "timezone-race",
                "expected_workshop_version": workshop.version,
                "timezone": "Europe/Paris",
            },
            idempotency_key="timezone-race",
        ),
    )
    assert ResultCode.SUCCESS in codes
    workshop.refresh_from_db()
    assert workshop.status == "operational"
    assert workshop.timezone in {"Europe/London", "Europe/Paris"}
    assert Event.objects.filter(event_type="WORKSHOP_BECAME_OPERATIONAL").count() == 1
    assert Event.objects.filter(event_type="USER_INVITATION_ACCEPTED").count() == 1
    assert Event.objects.filter(event_type="WORKSHOP_TIMEZONE_CHANGED").count() in {
        0,
        1,
    }
