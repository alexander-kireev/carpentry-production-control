from datetime import UTC, datetime

import pytest
from django.db import transaction

from events.models import EventNotificationIntent, Notification
from events.processing import process_event_notification_intents
from events.producer import EventSpec, produce_events
from tests.test_timezone_correction import make_admin_workshop


def produce(event_type, workshop_id, key):
    with transaction.atomic():
        return produce_events(
            [
                EventSpec(
                    event_type=event_type,
                    occurred_at=datetime.now(UTC),
                    actor_type="system",
                    actor_user_id=None,
                    primary_subject_type="workshop",
                    primary_subject_id=workshop_id,
                    payload={},
                    idempotency_key=key,
                )
            ]
        )[0]


@pytest.mark.django_db
def test_timezone_event_zero_recipient_is_successfully_processed():
    _, workshop = make_admin_workshop()
    event = produce("WORKSHOP_TIMEZONE_CHANGED", workshop.id, "zero")
    result = process_event_notification_intents(limit=10)
    event.notification_intent.refresh_from_db()
    assert result.processed == 1
    assert event.notification_intent.status == EventNotificationIntent.Status.PROCESSED
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_operational_event_routes_once_to_current_permanent_admin():
    admin, workshop = make_admin_workshop()
    event = produce("WORKSHOP_BECAME_OPERATIONAL", workshop.id, "operational")
    first = process_event_notification_intents(limit=10)
    second = process_event_notification_intents(limit=10)
    notification = Notification.objects.get(event=event)
    assert notification.recipient_user_id == admin.id
    assert (first.notifications, second.notifications) == (1, 0)


@pytest.mark.django_db
def test_routing_failure_retries_then_exhausts_without_payload_logging(caplog):
    from workshops.models import Workshop

    workshop = Workshop.objects.create(
        name="No admin",
        address="1 Test Street",
        email="no-admin@example.test",
        timezone="Europe/London",
        status=Workshop.Status.OPERATIONAL,
    )
    event = produce("WORKSHOP_BECAME_OPERATIONAL", workshop.id, "poison")
    for _ in range(3):
        process_event_notification_intents(limit=1)
    event.notification_intent.refresh_from_db()
    assert event.notification_intent.status == EventNotificationIntent.Status.FAILED
    assert event.notification_intent.attempts == 3
    assert "poison" not in caplog.text
    assert "Permanent administrator routing is unavailable" not in caplog.text
