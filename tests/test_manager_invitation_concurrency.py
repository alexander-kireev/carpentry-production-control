from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, transaction

from identity.commands import invite_permanent_manager
from identity.models import User
from identity.results import ResultCode
from tests.test_manager_invitation import attached_admin, payload

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "second_key,second_code",
    (
        ("winning-key", ResultCode.REPLAY),
        ("different-key", ResultCode.ALREADY_ADVANCED),
    ),
)
def test_workshop_lock_serializes_same_and_different_key_races(second_key, second_code):
    admin, _, _ = attached_admin()
    lock_held = Event()
    release = Event()
    second_entered = Event()

    def winner():
        close_old_connections()
        with transaction.atomic():
            result = invite_permanent_manager(
                actor_id=admin.id,
                data=payload(),
                idempotency_key="winning-key",
            )
            lock_held.set()
            assert release.wait(5)
        close_old_connections()
        return result.code

    def contender():
        close_old_connections()
        assert lock_held.wait(5)
        second_entered.set()
        result = invite_permanent_manager(
            actor_id=admin.id, data=payload(), idempotency_key=second_key
        )
        close_old_connections()
        return result.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(winner)
        second = pool.submit(contender)
        assert second_entered.wait(5)
        assert not second.done()
        release.set()
        assert first.result(timeout=10) == ResultCode.SUCCESS
        assert second.result(timeout=10) == second_code
    assert User.objects.filter(account_role="manager").count() == 1


def test_global_email_constraint_arbitrates_cross_workshop_race():
    first, _, _ = attached_admin(email="first-admin@example.test")
    second, _, _ = attached_admin(email="second-admin@example.test")
    start = Event()

    def invite(actor_id, key):
        close_old_connections()
        assert start.wait(5)
        result = invite_permanent_manager(
            actor_id=actor_id,
            data=payload(email="shared-manager@example.test"),
            idempotency_key=key,
        )
        close_old_connections()
        return result.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(invite, first.id, "first"),
            pool.submit(invite, second.id, "second"),
        ]
        start.set()
        codes = [future.result(timeout=10) for future in futures]
    assert codes.count(ResultCode.SUCCESS) == 1
    assert codes.count(ResultCode.INVITATION_UNAVAILABLE) == 1
    assert User.objects.filter(email="shared-manager@example.test").count() == 1
