from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from events.models import Event, EventNotificationIntent
from identity.commands import accept_permanent_manager_invitation
from identity.models import User, UserInvitation
from identity.results import ResultCode
from tests.test_invitation_acceptance import PASSWORD, acceptance_fixture
from workshops.models import Workshop


@pytest.mark.django_db(transaction=True)
def test_concurrent_acceptance_is_first_commit_wins():
    _, manager, workshop, invitation, token = acceptance_fixture()
    barrier = Barrier(2)

    def accept():
        close_old_connections()
        barrier.wait()
        try:
            return accept_permanent_manager_invitation(
                selector=str(invitation.id),
                raw_token=token,
                password=PASSWORD,
                expected_generation=1,
            ).code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: accept(), range(2)))
    assert codes.count(ResultCode.SUCCESS) == 1
    assert codes.count(ResultCode.INVITATION_UNAVAILABLE) == 1
    manager.refresh_from_db()
    workshop.refresh_from_db()
    invitation.refresh_from_db()
    assert (manager.status, manager.version) == (User.Status.ACTIVE, 2)
    assert invitation.status == UserInvitation.Status.CONSUMED
    assert (workshop.status, workshop.version) == (Workshop.Status.OPERATIONAL, 3)
    assert Event.objects.count() == EventNotificationIntent.objects.count() == 2
