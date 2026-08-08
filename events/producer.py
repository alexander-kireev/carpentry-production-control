from dataclasses import dataclass
from typing import Iterable

from django.db import models, transaction

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
        "MATERIAL_CREATED",
        "MATERIAL_UPDATED",
        "MATERIAL_RETIRED",
        "MATERIAL_RESTORED",
        "MATERIAL_VARIANT_CREATED",
        "MATERIAL_VARIANT_UPDATED",
        "MATERIAL_VARIANT_RETIRED",
        "MATERIAL_VARIANT_RESTORED",
        "MATERIAL_STOCK_REPLENISHED",
        "STATION_CREATED",
        "STATION_UPDATED",
        "STATION_RETIRED",
        "OPERATION_TYPE_CAPABILITY_LOST",
    }
)

CONFIGURATION_SUBJECTS = {
    "WORKSHOP_ROLE": "workshop_role",
    "OPERATION_TYPE": "operation_type",
    "UNIT_TYPE": "unit_type",
    "MATERIAL_CATEGORY": "material_category",
    "SHIFT_DEFINITION": "shift_definition",
}

MATERIAL_EVENT_TYPES = frozenset(
    {
        "MATERIAL_CREATED",
        "MATERIAL_UPDATED",
        "MATERIAL_RETIRED",
        "MATERIAL_RESTORED",
        "MATERIAL_VARIANT_CREATED",
        "MATERIAL_VARIANT_UPDATED",
        "MATERIAL_VARIANT_RETIRED",
        "MATERIAL_VARIANT_RESTORED",
        "MATERIAL_STOCK_REPLENISHED",
    }
)


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

    if spec.event_type in {
        "STATION_CREATED",
        "STATION_UPDATED",
        "STATION_RETIRED",
        "OPERATION_TYPE_CAPABILITY_LOST",
    }:
        _validate_station_subjects(spec)
        return
    if spec.event_type in MATERIAL_EVENT_TYPES:
        _validate_material_subjects(spec)
        return
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


def _validate_station_subjects(spec):
    from workshops.models import OperationType, Station, Workshop

    if spec.event_type == "OPERATION_TYPE_CAPABILITY_LOST":
        if spec.primary_subject_type != "operation_type" or not spec.primary_subject_id:
            raise ValueError("Invalid primary subject")
        operation_type = OperationType.objects.filter(
            pk=spec.primary_subject_id
        ).first()
        if operation_type is None or operation_type.workshop_id is None:
            raise ValueError("Invalid primary subject")
        if len(spec.subjects) != 2:
            raise ValueError("Invalid related subjects")
        workshop_subject, station_subject = spec.subjects
        station = Station.objects.filter(pk=station_subject.subject_id).first()
        if (
            workshop_subject
            != EventSubjectSpec("workshop", operation_type.workshop_id, "workshop")
            or station_subject.subject_type != "station"
            or station_subject.subject_role != "source_station"
            or station is None
            or station.workshop_id != operation_type.workshop_id
        ):
            raise ValueError("Invalid related subjects")
        return

    if spec.primary_subject_type != "station" or not spec.primary_subject_id:
        raise ValueError("Invalid primary subject")
    station = Station.objects.filter(pk=spec.primary_subject_id).first()
    if station is None or not Workshop.objects.filter(pk=station.workshop_id).exists():
        raise ValueError("Invalid primary subject")
    if not spec.subjects or spec.subjects[0] != EventSubjectSpec(
        "workshop", station.workshop_id, "workshop"
    ):
        raise ValueError("Invalid related subjects")
    operation_subjects = spec.subjects[1:]
    if spec.event_type != "STATION_UPDATED" and operation_subjects:
        raise ValueError("Invalid related subjects")
    operation_ids = [subject.subject_id for subject in operation_subjects]
    if operation_ids != sorted(set(operation_ids)):
        raise ValueError("Invalid related subjects")
    if any(
        subject.subject_type != "operation_type"
        or subject.subject_role != "changed_capability"
        for subject in operation_subjects
    ):
        raise ValueError("Invalid related subjects")
    if operation_ids:
        valid = OperationType.objects.filter(pk__in=operation_ids).filter(
            models.Q(workshop_id=station.workshop_id)
            | models.Q(workshop__isnull=True, machine_key="other")
        )
        if valid.count() != len(operation_ids):
            raise ValueError("Invalid related subjects")


def _validate_material_subjects(spec):
    from workshops.models import (
        Material,
        MaterialCategory,
        MaterialVariant,
        StockEffect,
        UnitType,
    )

    if (
        spec.event_type.startswith("MATERIAL_VARIANT_")
        or spec.event_type == "MATERIAL_STOCK_REPLENISHED"
    ):
        if (
            spec.primary_subject_type != "material_variant"
            or not spec.primary_subject_id
        ):
            raise ValueError("Invalid primary subject")
        try:
            variant = MaterialVariant.objects.select_related("material").get(
                pk=spec.primary_subject_id
            )
        except MaterialVariant.DoesNotExist as error:
            raise ValueError("Invalid primary subject") from error
        if spec.event_type == "MATERIAL_STOCK_REPLENISHED":
            if len(spec.subjects) != 1:
                raise ValueError("Invalid related subjects")
            subject = spec.subjects[0]
            if (
                subject.subject_type != "stock_effect"
                or subject.subject_role != "source_effect"
            ):
                raise ValueError("Invalid related subjects")
            effect = StockEffect.objects.filter(
                pk=subject.subject_id,
                workshop_id=variant.workshop_id,
                material_variant_id=variant.id,
                effect_type="opening_balance",
                source_type="material_variant_creation",
            ).first()
            if effect is None:
                raise ValueError("Invalid related subjects")
            return
        expected = (
            EventSubjectSpec("material", variant.material_id, "material"),
            EventSubjectSpec("unit_type", variant.material.unit_id, "unit_type"),
        )
        if tuple(spec.subjects) != expected:
            raise ValueError("Invalid related subjects")
        if not UnitType.objects.filter(
            pk=variant.material.unit_id, workshop_id=variant.workshop_id
        ).exists():
            raise ValueError("Invalid related subjects")
        return
    if spec.primary_subject_type != "material" or not spec.primary_subject_id:
        raise ValueError("Invalid primary subject")
    try:
        material = Material.objects.get(pk=spec.primary_subject_id)
    except Material.DoesNotExist as error:
        raise ValueError("Invalid primary subject") from error
    expected = (
        EventSubjectSpec(
            "material_category", material.category_id, "material_category"
        ),
    )
    if tuple(spec.subjects) != expected:
        raise ValueError("Invalid related subjects")
    category = MaterialCategory.objects.filter(pk=material.category_id).first()
    valid_category = category is not None and (
        category.workshop_id == material.workshop_id
        or (
            category.workshop_id is None
            and category.machine_key == "undefined"
            and category.name == "undefined"
            and category.status == "active"
        )
    )
    if not valid_category:
        raise ValueError("Invalid related subjects")


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
