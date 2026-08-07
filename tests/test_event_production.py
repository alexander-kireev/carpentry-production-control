from datetime import UTC, datetime

import pytest
from django.db import IntegrityError, transaction

from events.models import Event, EventNotificationIntent, EventSubject
from events.producer import EventSpec, EventSubjectSpec, produce_events
from tests.test_library_commands import library_admin
from workshops.models import UnitType


def spec(key="key", event_type="WORKSHOP_TIMEZONE_CHANGED"):
    return EventSpec(
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        actor_type="system",
        actor_user_id=None,
        primary_subject_type="workshop",
        primary_subject_id=1,
        payload={"old_timezone": "UTC", "new_timezone": "Europe/London"},
        idempotency_key=key,
    )


@pytest.mark.django_db(transaction=True)
def test_producer_requires_source_transaction_and_closed_catalogue():
    with pytest.raises(RuntimeError):
        produce_events([spec()])
    with transaction.atomic(), pytest.raises(ValueError):
        produce_events([spec(event_type="NOT_APPROVED")])


@pytest.mark.django_db
def test_every_event_gets_exactly_one_intent_and_sequence():
    with transaction.atomic():
        created = produce_events([spec("one"), spec("two")])
    assert [event.sequence_number for event in created] == sorted(
        event.sequence_number for event in created
    )
    assert EventNotificationIntent.objects.count() == Event.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_producer_key_cannot_duplicate():
    with transaction.atomic():
        produce_events([spec("same")])
    with pytest.raises(IntegrityError), transaction.atomic():
        produce_events([spec("same")])


@pytest.mark.django_db
def test_acceptance_sibling_types_remain_in_closed_catalogue():
    with transaction.atomic():
        produced = produce_events(
            [
                spec("accepted", "USER_INVITATION_ACCEPTED"),
                spec("operational", "WORKSHOP_BECAME_OPERATIONAL"),
            ]
        )
    assert len(produced) == 2
    assert EventNotificationIntent.objects.count() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "subjects_factory",
    [
        lambda workshop, foreign: (
            EventSubjectSpec("workshop", workshop.id, "observer"),
        ),
        lambda workshop, foreign: (
            EventSubjectSpec("workshop", foreign.id, "workshop"),
        ),
    ],
)
def test_configuration_producer_rejects_closed_role_and_foreign_workshop_subject(
    subjects_factory,
):
    actor, workshop = library_admin("event-subject")
    _, foreign = library_admin("event-subject-foreign")
    source = UnitType.objects.create(workshop=workshop, name="Metres", abbreviation="m")
    event_spec = EventSpec(
        event_type="UNIT_TYPE_CREATED",
        occurred_at=datetime.now(UTC),
        actor_type="user",
        actor_user_id=actor.id,
        primary_subject_type="unit_type",
        primary_subject_id=source.id,
        payload={"version": 1},
        idempotency_key=f"rejected-{subjects_factory.__name__}-{foreign.id}",
        subjects=subjects_factory(workshop, foreign),
    )
    before = (
        Event.objects.count(),
        EventSubject.objects.count(),
        EventNotificationIntent.objects.count(),
    )
    with transaction.atomic(), pytest.raises(ValueError, match="related subjects"):
        produce_events([event_spec])
    assert (
        Event.objects.count(),
        EventSubject.objects.count(),
        EventNotificationIntent.objects.count(),
    ) == before
