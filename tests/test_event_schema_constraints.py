from datetime import UTC, datetime

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from events.models import Event, EventSubject


def create_event(**overrides):
    values = {
        "event_type": "WORKSHOP_TIMEZONE_CHANGED",
        "occurred_at": datetime.now(UTC),
        "actor_type": "system",
        "payload": {},
        "idempotency_key": "schema-key",
    }
    values.update(overrides)
    return Event.objects.create(**values)


@pytest.mark.django_db(transaction=True)
def test_event_actor_shape_and_producer_key_are_database_enforced():
    with pytest.raises(IntegrityError), transaction.atomic():
        create_event(actor_type="user", actor_user=None)
    create_event()
    with pytest.raises(IntegrityError), transaction.atomic():
        create_event()


@pytest.mark.django_db(transaction=True)
def test_event_rows_reject_update_and_delete():
    event = create_event()
    with pytest.raises(DatabaseError), transaction.atomic():
        Event.objects.filter(pk=event.pk).update(payload={"changed": True})
    with pytest.raises(DatabaseError), transaction.atomic():
        Event.objects.filter(pk=event.pk).delete()


@pytest.mark.django_db
def test_event_boundary_tables_exist():
    assert {
        "event",
        "event_subject",
        "event_notification_intent",
        "notification",
    } <= set(connection.introspection.table_names())


@pytest.mark.django_db(transaction=True)
def test_event_subject_identity_and_immutability_are_database_enforced():
    event = create_event()
    subject = EventSubject.objects.create(
        event=event,
        subject_type="workshop",
        subject_id=1,
        subject_role="workshop",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        EventSubject.objects.create(
            event=event,
            subject_type="workshop",
            subject_id=1,
            subject_role="workshop",
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        EventSubject.objects.filter(pk=subject.pk).update(subject_role="changed")
    with pytest.raises(DatabaseError), transaction.atomic():
        EventSubject.objects.filter(pk=subject.pk).delete()
