from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from events.models import Event
from identity.commands import correct_workshop_timezone
from identity.results import ResultCode
from tests.test_timezone_correction import correction_data, make_admin_workshop


@pytest.mark.django_db(transaction=True)
def test_concurrent_timezone_corrections_serialize_to_one_event():
    user, workshop = make_admin_workshop()
    barrier = Barrier(2)

    def submit(key, zone):
        close_old_connections()
        barrier.wait()
        try:
            return correct_workshop_timezone(
                actor_id=user.id,
                data=correction_data(workshop, zone=zone),
                idempotency_key=key,
            ).code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(
            pool.map(
                lambda args: submit(*args),
                (("key-a", "Europe/Paris"), ("key-b", "Europe/Berlin")),
            )
        )
    assert codes.count(ResultCode.SUCCESS) == 1
    assert Event.objects.filter(event_type="WORKSHOP_TIMEZONE_CHANGED").count() == 1
