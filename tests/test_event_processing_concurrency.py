from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from events.models import EventNotificationIntent
from events.processing import process_event_notification_intents
from tests.test_event_processing import produce
from tests.test_timezone_correction import make_admin_workshop


@pytest.mark.django_db(transaction=True)
def test_multiworker_claims_are_disjoint_and_complete():
    _, workshop = make_admin_workshop()
    for number in range(6):
        produce("WORKSHOP_TIMEZONE_CHANGED", workshop.id, f"work-{number}")
    barrier = Barrier(2)

    def run_worker():
        close_old_connections()
        barrier.wait()
        try:
            return process_event_notification_intents(limit=3)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(run_worker) for _ in range(2)]
        counts = [future.result() for future in results]
    assert sum(result.claimed for result in counts) == 6
    assert EventNotificationIntent.objects.filter(status="processed").count() == 6
