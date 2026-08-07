from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from .models import Event, EventNotificationIntent, EventSubject

ALLOWED_EVENT_TYPES = frozenset(
    {
        "WORKSHOP_TIMEZONE_CHANGED",
        "USER_INVITATION_ACCEPTED",
        "WORKSHOP_BECAME_OPERATIONAL",
        "WORKSHOP_ROLE_CREATED",
        "WORKSHOP_ROLE_UPDATED",
        "WORKSHOP_ROLE_RETIRED",
        "WORKSHOP_ROLE_RESTORED",
        "OPERATION_TYPE_CREATED",
        "OPERATION_TYPE_UPDATED",
        "OPERATION_TYPE_RETIRED",
        "OPERATION_TYPE_RESTORED",
        "UNIT_TYPE_CREATED",
        "UNIT_TYPE_UPDATED",
        "UNIT_TYPE_RETIRED",
        "UNIT_TYPE_RESTORED",
        "MATERIAL_CATEGORY_CREATED",
        "MATERIAL_CATEGORY_UPDATED",
        "MATERIAL_CATEGORY_RETIRED",
        "MATERIAL_CATEGORY_RESTORED",
        "SHIFT_DEFINITION_CREATED",
        "SHIFT_DEFINITION_EDITED",
        "SHIFT_DEFINITION_RETIRED",
        "SHIFT_DEFINITION_RESTORED",
    }
)

CONFIGURATION_SUBJECTS = {
    "WORKSHOP_ROLE": "workshop_role",
    "OPERATION_TYPE": "operation_type",
    "UNIT_TYPE": "unit_type",
    "MATERIAL_CATEGORY": "material_category",
    "SHIFT_DEFINITION": "shift_definition",
}


@dataclass(frozen=True)
class EventSubjectSpec:
    subject_type: str
    subject_id: int
    subject_role: str


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
    subjects: tuple[EventSubjectSpec, ...] = ()


def _validate_configuration_subjects(spec):
    from workshops.models import (
        MaterialCategory,
        OperationType,
        ShiftDefinition,
        UnitType,
        Workshop,
        WorkshopRole,
    )

    prefix = next(
        (
            prefix
            for prefix in CONFIGURATION_SUBJECTS
            if spec.event_type.startswith(prefix)
        ),
        None,
    )
    if prefix is None:
        if spec.subjects:
            raise ValueError("Event subjects are not catalogued for this event")
        return
    expected_type = CONFIGURATION_SUBJECTS[prefix]
    if spec.primary_subject_type != expected_type or not spec.primary_subject_id:
        raise ValueError("Invalid primary subject")
    models_by_type = {
        "workshop_role": WorkshopRole,
        "operation_type": OperationType,
        "unit_type": UnitType,
        "material_category": MaterialCategory,
        "shift_definition": ShiftDefinition,
    }
    try:
        source = models_by_type[expected_type].objects.get(pk=spec.primary_subject_id)
    except models_by_type[expected_type].DoesNotExist as error:
        raise ValueError("Invalid primary subject") from error
    if source.workshop_id is None:
        raise ValueError("Protected subjects cannot be event sources")
    expected_subjects = (
        ()
        if prefix == "SHIFT_DEFINITION"
        else (EventSubjectSpec("workshop", source.workshop_id, "workshop"),)
    )
    if tuple(spec.subjects) != expected_subjects:
        raise ValueError("Invalid related subjects")
    if not Workshop.objects.filter(pk=source.workshop_id).exists():
        raise ValueError("Invalid Workshop subject")


def produce_events(specs: Iterable[EventSpec]):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Events must be produced inside the source transaction")
    created = []
    for spec in specs:
        if spec.event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("Unsupported event type")
        if not spec.idempotency_key:
            raise ValueError("Event idempotency key is required")
        _validate_configuration_subjects(spec)
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
        EventSubject.objects.bulk_create(
            [
                EventSubject(
                    event=event,
                    subject_type=subject.subject_type,
                    subject_id=subject.subject_id,
                    subject_role=subject.subject_role,
                )
                for subject in spec.subjects
            ]
        )
        created.append(event)
    return created
