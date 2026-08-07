import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from datetime import time

from django.db import IntegrityError, transaction
from django.utils import timezone

from events.producer import EventSpec, EventSubjectSpec, produce_events
from identity.models import User

from .models import (
    ConfigurationCommandReceipt,
    MaterialCategory,
    OperationType,
    ShiftDefinition,
    UnitType,
    Workshop,
    WorkshopRole,
    WorkshopRoleDefaultClearance,
)
from .protected_configuration import (
    ProtectedConfigurationError,
    resolve_protected_configuration,
    verify_workshop_protected_pair,
)


@dataclass(frozen=True)
class LibraryCommandResult:
    code: str
    family: str | None = None
    result_id: int | None = None
    version: int | None = None
    errors: dict[str, list[str]] = field(default_factory=dict)


FAMILY_MODELS = {
    "workshop_role": WorkshopRole,
    "operation_type": OperationType,
    "unit_type": UnitType,
    "material_category": MaterialCategory,
    "shift_definition": ShiftDefinition,
}
EVENT_PREFIXES = {
    "workshop_role": "WORKSHOP_ROLE",
    "operation_type": "OPERATION_TYPE",
    "unit_type": "UNIT_TYPE",
    "material_category": "MATERIAL_CATEGORY",
    "shift_definition": "SHIFT_DEFINITION",
}
CREATE_FIELDS = {
    "workshop_role": ("name", "description"),
    "operation_type": (
        "name",
        "description",
        "is_production",
        "requires_clearance",
    ),
    "unit_type": ("name", "abbreviation"),
    "material_category": ("name",),
    "shift_definition": ("name", "start_time", "end_time", "days"),
}


def _normal(value):
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.strip())
    if isinstance(value, (list, tuple, set)):
        return sorted({_normal(item) for item in value})
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _positive_integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Version evidence is invalid")
    try:
        normalized = int(value)
    except ValueError as error:
        raise ValueError("Version evidence is invalid") from error
    if normalized <= 0 or isinstance(value, str) and str(normalized) != value.strip():
        raise ValueError("Version evidence is invalid")
    return normalized


def _clean_command_data(family, data, *, partial=False):
    if family not in FAMILY_MODELS or not isinstance(data, dict):
        raise ValueError("Invalid library data")
    cleaned = {}
    for key in CREATE_FIELDS[family]:
        if key not in data:
            if partial:
                continue
            raise ValueError("Missing library field")
        value = _normal(data[key])
        if key == "name" and (not isinstance(value, str) or not value):
            raise ValueError("Name is required")
        if key == "description":
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError("Description is invalid")
        if key == "abbreviation" and (not isinstance(value, str) or not value):
            raise ValueError("Abbreviation is required")
        if key in {"is_production", "requires_clearance"} and not isinstance(
            value, bool
        ):
            raise ValueError("Classification is invalid")
        if key in {"start_time", "end_time"}:
            if isinstance(value, str):
                value = time.fromisoformat(value)
            if not isinstance(value, time):
                raise ValueError("Time is invalid")
        if key == "days":
            try:
                value = sorted({int(day) for day in value})
            except (TypeError, ValueError) as error:
                raise ValueError("Days are invalid") from error
            if not value or any(day < 0 or day > 6 for day in value):
                raise ValueError("Days are invalid")
        cleaned[key] = value
    if (
        family == "shift_definition"
        and {"start_time", "end_time"} <= cleaned.keys()
        and cleaned["start_time"] >= cleaned["end_time"]
    ):
        raise ValueError("Shift times are invalid")
    if family == "workshop_role":
        has_ids = "default_clearance_ids" in data
        has_versions = "default_clearance_versions" in data
        if (not partial and not has_ids) or has_ids != has_versions:
            raise ValueError("Default clearance evidence is incomplete")
        if not has_ids:
            return cleaned
        if isinstance(data["default_clearance_ids"], (str, bytes)):
            raise ValueError("Default clearances are invalid")
        try:
            if any(isinstance(value, bool) for value in data["default_clearance_ids"]):
                raise ValueError("Default clearances are invalid")
            cleaned["default_clearance_ids"] = sorted(
                {int(value) for value in data["default_clearance_ids"]}
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Default clearances are invalid") from error
        raw_versions = data["default_clearance_versions"]
        if not isinstance(raw_versions, dict):
            raise ValueError("Default clearance evidence is invalid")
        normalized_versions = {}
        for raw_id, raw_version in raw_versions.items():
            operation_type_id = _positive_integer(raw_id)
            if operation_type_id in normalized_versions:
                raise ValueError("Default clearance evidence is invalid")
            normalized_versions[operation_type_id] = _positive_integer(raw_version)
        if set(normalized_versions) != set(cleaned["default_clearance_ids"]):
            raise ValueError("Default clearance evidence is incomplete")
        cleaned["default_clearance_versions"] = {
            str(key): normalized_versions[key] for key in sorted(normalized_versions)
        }
    return cleaned


def _fingerprint(family, data):
    meaningful = {key: _normal(data.get(key)) for key in CREATE_FIELDS[family]}
    if family == "workshop_role":
        meaningful["default_clearance_ids"] = _normal(
            data.get("default_clearance_ids", ())
        )
        meaningful["default_clearance_versions"] = {
            str(key): value
            for key, value in sorted(data.get("default_clearance_versions", {}).items())
        }
    encoded = json.dumps(
        meaningful, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest(), meaningful


def _locked_admin(actor_id, workshop_id):
    workshop = Workshop.objects.select_for_update().filter(pk=workshop_id).first()
    actor = (
        User.objects.select_for_update(of=("self",))
        .select_related("workshop_role")
        .filter(pk=actor_id)
        .first()
    )
    if workshop is None or actor is None:
        return None, None
    try:
        resolve_protected_configuration()
        verify_workshop_protected_pair(workshop)
    except ProtectedConfigurationError:
        return None, None
    role = actor.workshop_role
    allowed_stage = workshop.status in {
        Workshop.Status.MANAGER_ACTIVATION_PENDING,
        Workshop.Status.OPERATIONAL,
    }
    exact = (
        actor.status == User.Status.ACTIVE
        and actor.account_role == User.AccountRole.ADMIN
        and actor.onboarding_state is None
        and actor.workshop_id == workshop.id
        and role is not None
        and role.workshop_id is None
        and role.machine_key == "admin"
        and role.name == "Admin"
        and role.status == WorkshopRole.Status.ACTIVE
    )
    return (actor, workshop) if exact and allowed_stage else (None, None)


def _locked_clearances(workshop, data):
    ids = data["default_clearance_ids"]
    selected = list(
        OperationType.objects.select_for_update().filter(pk__in=ids).order_by("id")
    )
    if len(selected) != len(ids):
        raise IntegrityError("invalid clearance selection")
    expected_versions = data["default_clearance_versions"]
    for operation_type in selected:
        allowed = operation_type.workshop_id == workshop.id or (
            operation_type.workshop_id is None
            and operation_type.machine_key == "other"
            and operation_type.name == "Other"
            and operation_type.status == OperationType.Status.ACTIVE
        )
        if (
            not allowed
            or operation_type.version != expected_versions[str(operation_type.id)]
        ):
            raise IntegrityError("invalid clearance selection")
    return selected


def _event_subjects(family, workshop_id):
    if family == "shift_definition":
        return ()
    return (EventSubjectSpec("workshop", workshop_id, "workshop"),)


def _produce(family, action, actor, source, changed_fields):
    suffix = (
        "EDITED" if family == "shift_definition" and action == "UPDATED" else action
    )
    return produce_events(
        [
            EventSpec(
                event_type=f"{EVENT_PREFIXES[family]}_{suffix}",
                occurred_at=timezone.now(),
                actor_type="user",
                actor_user_id=actor.id,
                primary_subject_type=family,
                primary_subject_id=source.id,
                payload={
                    "version": source.version,
                    "changed_fields": sorted(changed_fields),
                    "status": source.status,
                },
                idempotency_key=f"{family}:{source.id}:{suffix.lower()}:{source.version}",
                subjects=_event_subjects(family, source.workshop_id),
            )
        ]
    )[0]


def _safe_source(family, workshop_id, result_id):
    return (
        FAMILY_MODELS[family]
        .objects.filter(pk=result_id, workshop_id=workshop_id)
        .first()
    )


def create_library_item(*, actor_id, workshop_id, family, submission_key, data):
    if family not in FAMILY_MODELS or not submission_key:
        return LibraryCommandResult("unavailable")
    try:
        cleaned = _clean_command_data(family, data)
        fingerprint, normalized = _fingerprint(family, cleaned)
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return LibraryCommandResult("unavailable")
            command_type = f"{family}_create"
            receipt = ConfigurationCommandReceipt.objects.filter(
                workshop=workshop,
                command_type=command_type,
                submission_key=submission_key,
            ).first()
            if receipt is not None:
                source = _safe_source(family, workshop.id, receipt.result_id)
                if (
                    receipt.actor_user_id == actor.id
                    and receipt.payload_fingerprint == fingerprint
                    and receipt.fingerprint_version == 1
                    and receipt.result_type == family
                    and source is not None
                ):
                    return LibraryCommandResult(
                        "replay", family, source.id, source.version
                    )
                return LibraryCommandResult("unavailable")

            selected = (
                _locked_clearances(workshop, normalized)
                if family == "workshop_role"
                else ()
            )
            values = {key: normalized[key] for key in CREATE_FIELDS[family]}
            if family == "shift_definition":
                values["days"] = [int(day) for day in values["days"]]
            source = FAMILY_MODELS[family].objects.create(workshop=workshop, **values)
            if family == "workshop_role":
                for operation_type in selected:
                    WorkshopRoleDefaultClearance.objects.create(
                        workshop_role=source, operation_type=operation_type
                    )
                    if operation_type.first_referenced_at is None:
                        operation_type.first_referenced_at = timezone.now()
                        operation_type.save(update_fields=["first_referenced_at"])
            _produce(family, "CREATED", actor, source, CREATE_FIELDS[family])
            ConfigurationCommandReceipt.objects.create(
                workshop=workshop,
                actor_user=actor,
                command_type=command_type,
                submission_key=submission_key,
                fingerprint_version=1,
                payload_fingerprint=fingerprint,
                result_type=family,
                result_id=source.id,
                result_summary={"family": family, "id": source.id, "version": 1},
            )
            return LibraryCommandResult("success", family, source.id, source.version)
    except IntegrityError, TypeError, ValueError:
        return LibraryCommandResult("validation_error")


def edit_library_item(
    *, actor_id, workshop_id, family, item_id, expected_version, data
):
    if family not in FAMILY_MODELS:
        return LibraryCommandResult("unavailable")
    try:
        cleaned_data = _clean_command_data(family, data, partial=True)
        if not cleaned_data:
            return LibraryCommandResult("validation_error")
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return LibraryCommandResult("unavailable")
            source = (
                FAMILY_MODELS[family]
                .objects.select_for_update()
                .filter(pk=item_id, workshop=workshop)
                .first()
            )
            if source is None or getattr(source, "machine_key", None) is not None:
                return LibraryCommandResult("unavailable")
            if source.version != expected_version:
                return LibraryCommandResult("stale", family, source.id, source.version)
            changed = []
            for key in CREATE_FIELDS[family]:
                if (
                    key not in cleaned_data
                    or family == "workshop_role"
                    and key == "default_clearance_ids"
                ):
                    continue
                value = cleaned_data[key]
                if (
                    family == "operation_type"
                    and source.first_referenced_at is not None
                    and key in {"is_production", "requires_clearance"}
                    and getattr(source, key) != value
                ):
                    return LibraryCommandResult("unavailable")
                if getattr(source, key) != value:
                    setattr(source, key, value)
                    changed.append(key)
            if family == "workshop_role" and "default_clearance_ids" in cleaned_data:
                ids = cleaned_data["default_clearance_ids"]
                selected = _locked_clearances(workshop, cleaned_data)
                current_ids = set(
                    source.default_clearance_links.values_list(
                        "operation_type_id", flat=True
                    )
                )
                if current_ids != set(ids):
                    source.default_clearance_links.all().delete()
                    for operation_type in selected:
                        WorkshopRoleDefaultClearance.objects.create(
                            workshop_role=source, operation_type=operation_type
                        )
                        if operation_type.first_referenced_at is None:
                            operation_type.first_referenced_at = timezone.now()
                            operation_type.save(update_fields=["first_referenced_at"])
                    changed.append("default_clearances")
            if not changed:
                return LibraryCommandResult(
                    "success", family, source.id, source.version
                )
            source.version += 1
            source.save(
                update_fields=[
                    *(field for field in changed if field != "default_clearances"),
                    "version",
                ]
            )
            _produce(family, "UPDATED", actor, source, changed)
            return LibraryCommandResult("success", family, source.id, source.version)
    except IntegrityError, TypeError, ValueError:
        return LibraryCommandResult("validation_error")


def transition_library_item(
    *, actor_id, workshop_id, family, item_id, expected_version, action
):
    if family not in FAMILY_MODELS or action not in {"retire", "restore"}:
        return LibraryCommandResult("unavailable")
    try:
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return LibraryCommandResult("unavailable")
            source = (
                FAMILY_MODELS[family]
                .objects.select_for_update()
                .filter(pk=item_id, workshop=workshop)
                .first()
            )
            if source is None or getattr(source, "machine_key", None) is not None:
                return LibraryCommandResult("unavailable")
            if source.version != expected_version:
                return LibraryCommandResult("stale", family, source.id, source.version)
            target = "retired" if action == "retire" else "active"
            if source.status == target:
                return LibraryCommandResult(
                    "success", family, source.id, source.version
                )
            if (
                family == "workshop_role"
                and action == "retire"
                and source.users.filter(
                    status__in=(User.Status.PENDING, User.Status.ACTIVE)
                ).exists()
            ):
                return LibraryCommandResult("unavailable")
            if (
                family == "operation_type"
                and action == "retire"
                and source.default_role_links.exists()
            ):
                return LibraryCommandResult("unavailable")
            source.status = target
            source.version += 1
            source.save(update_fields=["status", "version"])
            _produce(
                family,
                "RETIRED" if action == "retire" else "RESTORED",
                actor,
                source,
                ["status"],
            )
            return LibraryCommandResult("success", family, source.id, source.version)
    except IntegrityError, TypeError, ValueError:
        return LibraryCommandResult("validation_error")
