from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from .models import Event, EventNotificationIntent

ALLOWED_EVENT_TYPES = frozenset(
    {
        "WORKSHOP_TIMEZONE_CHANGED",
        "USER_INVITATION_ACCEPTED",
        "WORKSHOP_BECAME_OPERATIONAL",
    }
)


@dataclass(frozen=True)
class EventSpec:
    event_type: str
    occurred_at: object
    actor_type: str
    actor_user_id: int | None
    primary_subject_type: str | None
    primary_subject_id: int | None
    payload: dict
    idempotency_key: str
    correlation_key: str | None = None
    causation_event_id: int | None = None


def produce_events(specs: Iterable[EventSpec]):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Events must be produced inside the source transaction")
    created = []
    for spec in specs:
        if spec.event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("Unsupported event type")
        if not spec.idempotency_key:
            raise ValueError("Event idempotency key is required")
        event = Event.objects.create(
            event_type=spec.event_type,
            occurred_at=spec.occurred_at,
            actor_type=spec.actor_type,
            actor_user_id=spec.actor_user_id,
            primary_subject_type=spec.primary_subject_type,
            primary_subject_id=spec.primary_subject_id,
            payload=spec.payload,
            idempotency_key=spec.idempotency_key,
            correlation_key=spec.correlation_key,
            causation_event_id=spec.causation_event_id,
        )
        EventNotificationIntent.objects.create(event=event)
        created.append(event)
    return created
