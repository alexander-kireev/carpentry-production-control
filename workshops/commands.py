import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from events.producer import EventSpec, EventSubjectSpec, produce_events
from identity.models import User

from .models import (
    ConfigurationCommandReceipt,
    Material,
    MaterialCategory,
    MaterialCommandReceipt,
    MaterialVariant,
    OperationType,
    ShiftDefinition,
    StockEffect,
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
            if (
                family == "unit_type"
                and action == "retire"
                and source.materials.filter(status=Material.Status.ACTIVE).exists()
            ):
                return LibraryCommandResult("unavailable")
            if (
                family == "material_category"
                and action == "retire"
                and source.materials.filter(status=Material.Status.ACTIVE).exists()
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


@dataclass(frozen=True)
class MaterialCommandResult:
    code: str
    material_id: int | None = None
    material_version: int | None = None
    variant_id: int | None = None
    variant_version: int | None = None
    opening_effect_id: int | None = None
    errors: dict[str, list[str]] = field(default_factory=dict)


def _material_text(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    value = unicodedata.normalize("NFC", value.strip())
    if not value:
        raise ValueError(f"{label} is required")
    return value


def _material_decimal(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{label} is invalid") from error
    if not number.is_finite() or number < 0 or number.as_tuple().exponent < -4:
        raise ValueError(f"{label} is invalid")
    normalized = number.quantize(Decimal("0.0001"))
    if len(normalized.as_tuple().digits) > 14:
        raise ValueError(f"{label} is invalid")
    return normalized


def _material_fingerprint(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _locked_material_dependencies(
    workshop, *, category_id, category_version, unit_id, unit_version
):
    category_id = _positive_integer(category_id)
    category_version = _positive_integer(category_version)
    unit_id = _positive_integer(unit_id)
    unit_version = _positive_integer(unit_version)
    category = (
        MaterialCategory.objects.select_for_update().filter(pk=category_id).first()
    )
    unit = UnitType.objects.select_for_update().filter(pk=unit_id).first()
    allowed_category = (
        category is not None
        and category.status == MaterialCategory.Status.ACTIVE
        and (
            category.workshop_id == workshop.id
            or (
                category.workshop_id is None
                and category.machine_key == "undefined"
                and category.name == "undefined"
            )
        )
    )
    if (
        not allowed_category
        or category.version != category_version
        or unit is None
        or unit.workshop_id != workshop.id
        or unit.status != UnitType.Status.ACTIVE
        or unit.version != unit_version
    ):
        raise IntegrityError("invalid material dependencies")
    return category, unit


def _material_event(actor, material, action, changed_fields):
    return produce_events(
        [
            EventSpec(
                event_type=f"MATERIAL_{action}",
                occurred_at=timezone.now(),
                actor_type="user",
                actor_user_id=actor.id,
                primary_subject_type="material",
                primary_subject_id=material.id,
                payload={
                    "version": material.version,
                    "category_id": material.category_id,
                    "changed_fields": sorted(changed_fields),
                    "status": material.status,
                },
                idempotency_key=f"material:{material.id}:{action.lower()}:{material.version}",
                subjects=(
                    EventSubjectSpec(
                        "material_category", material.category_id, "material_category"
                    ),
                ),
            )
        ]
    )[0]


def _variant_event(actor, variant, action, changed_fields):
    material = Material.objects.get(pk=variant.material_id)
    return produce_events(
        [
            EventSpec(
                event_type=f"MATERIAL_VARIANT_{action}",
                occurred_at=timezone.now(),
                actor_type="user",
                actor_user_id=actor.id,
                primary_subject_type="material_variant",
                primary_subject_id=variant.id,
                payload={
                    "version": variant.version,
                    "changed_fields": sorted(changed_fields),
                    "status": variant.status,
                },
                idempotency_key=f"material_variant:{variant.id}:{action.lower()}:{variant.version}",
                subjects=(
                    EventSubjectSpec("material", material.id, "material"),
                    EventSubjectSpec("unit_type", material.unit_id, "unit_type"),
                ),
            )
        ]
    )[0]


def _opening_effect(
    *, workshop, actor, variant, quantity, submission_key, command_type
):
    effect = StockEffect.objects.create(
        workshop=workshop,
        material_variant=variant,
        effect_type="opening_balance",
        source_type="material_variant_creation",
        command_identity=submission_key,
        correlation_identity=f"workshop:{workshop.id}:{command_type}:{submission_key}",
        source_identity=None,
        source_version=None,
        actor_or_system=actor,
        delta=quantity,
        balance_before=Decimal("0.0000"),
        balance_after=quantity,
        reason=None,
        category=None,
        stock_projection_version=2,
    )
    variant.refresh_from_db(fields=("current_stock", "version"))
    if variant.version != 2 or variant.current_stock != quantity:
        raise IntegrityError("opening projection did not synchronize")
    return effect


def _replenishment_event(actor, variant, effect):
    return produce_events(
        [
            EventSpec(
                event_type="MATERIAL_STOCK_REPLENISHED",
                occurred_at=timezone.now(),
                actor_type="user",
                actor_user_id=actor.id,
                primary_subject_type="material_variant",
                primary_subject_id=variant.id,
                payload={
                    "version": variant.version,
                    "current_stock": str(variant.current_stock),
                    "source_category": "opening_balance",
                },
                idempotency_key=f"material_variant:{variant.id}:replenished:{variant.version}",
                subjects=(
                    EventSubjectSpec("stock_effect", effect.id, "source_effect"),
                ),
            )
        ]
    )[0]


def _opening_effect_is_exact(
    *,
    effect,
    workshop,
    actor,
    variant,
    opening_quantity,
    submission_key,
    command_type,
):
    correlation_identity = f"workshop:{workshop.id}:{command_type}:{submission_key}"
    return (
        effect is not None
        and effect.workshop_id == workshop.id
        and effect.material_variant_id == variant.id
        and effect.effect_type == "opening_balance"
        and effect.source_type == "material_variant_creation"
        and effect.command_identity == submission_key
        and effect.correlation_identity == correlation_identity
        and effect.source_identity is None
        and effect.source_version is None
        and effect.actor_or_system_id == actor.id
        and effect.delta == opening_quantity
        and effect.balance_before == Decimal("0.0000")
        and effect.balance_after == opening_quantity
        and effect.reason is None
        and effect.category is None
        and effect.stock_projection_version == 2
        and StockEffect.objects.filter(
            workshop=workshop, correlation_identity=correlation_identity
        ).count()
        == 1
    )


def _recover_material_create(
    receipt,
    workshop,
    actor,
    fingerprint,
    *,
    first_variant,
    submission_key,
):
    if (
        receipt.actor_user_id != actor.id
        or receipt.fingerprint_version != 1
        or receipt.payload_fingerprint != fingerprint
        or receipt.result_type != "material"
        or not isinstance(receipt.result_summary, dict)
    ):
        return MaterialCommandResult("unavailable")
    summary = receipt.result_summary
    bare_keys = {"material_id", "material_version"}
    combined_keys = bare_keys | {
        "first_variant_id",
        "first_variant_version",
        "opening_effect_id",
    }
    if set(summary) not in {frozenset(bare_keys), frozenset(combined_keys)}:
        return MaterialCommandResult("unavailable")
    material = Material.objects.filter(
        pk=summary.get("material_id"), workshop=workshop
    ).first()
    if (
        material is None
        or receipt.result_id != material.id
        or summary.get("material_version") != 1
    ):
        return MaterialCommandResult("unavailable")
    if set(summary) == bare_keys:
        correlation_identity = (
            f"workshop:{workshop.id}:material_create:{submission_key}"
        )
        if (
            first_variant is not None
            or StockEffect.objects.filter(
                workshop=workshop, correlation_identity=correlation_identity
            ).exists()
        ):
            return MaterialCommandResult("unavailable")
        return MaterialCommandResult("recovered", material.id, 1)
    if first_variant is None:
        return MaterialCommandResult("unavailable")
    variant = MaterialVariant.objects.filter(
        pk=summary.get("first_variant_id"), material=material, workshop=workshop
    ).first()
    effect = StockEffect.objects.filter(pk=summary.get("opening_effect_id")).first()
    if (
        variant is None
        or summary.get("first_variant_version") != 2
        or not _opening_effect_is_exact(
            effect=effect,
            workshop=workshop,
            actor=actor,
            variant=variant,
            opening_quantity=first_variant["opening_quantity"],
            submission_key=submission_key,
            command_type="material_create",
        )
    ):
        return MaterialCommandResult("unavailable")
    return MaterialCommandResult("recovered", material.id, 1, variant.id, 2, effect.id)


def create_material(*, actor_id, workshop_id, submission_key, data):
    if not submission_key or not isinstance(data, dict):
        return MaterialCommandResult("unavailable")
    try:
        name = _material_text(data.get("name"), "Name")
        category_id = _positive_integer(data.get("category_id"))
        category_version = _positive_integer(data.get("category_version"))
        unit_id = _positive_integer(data.get("unit_id"))
        unit_version = _positive_integer(data.get("unit_version"))
        variant_fields = ("spec_label", "opening_quantity", "min_threshold")
        present = [
            key in data and data[key] not in (None, "") for key in variant_fields
        ]
        if any(present) and not all(present):
            raise ValueError("First Variant fields are all required")
        first_variant = None
        if all(present):
            first_variant = {
                "spec_label": _material_text(data["spec_label"], "Variant label"),
                "opening_quantity": _material_decimal(
                    data["opening_quantity"], "Opening quantity"
                ),
                "min_threshold": _material_decimal(
                    data["min_threshold"], "Minimum threshold"
                ),
            }
        normalized = {
            "name": name,
            "category_id": category_id,
            "category_version": category_version,
            "unit_id": unit_id,
            "unit_version": unit_version,
            "first_variant": None
            if first_variant is None
            else {key: str(value) for key, value in first_variant.items()},
        }
        fingerprint = _material_fingerprint(normalized)
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return MaterialCommandResult("unavailable")
            receipt = ConfigurationCommandReceipt.objects.filter(
                workshop=workshop,
                command_type="material_create",
                submission_key=submission_key,
            ).first()
            if receipt is not None:
                return _recover_material_create(
                    receipt,
                    workshop,
                    actor,
                    fingerprint,
                    first_variant=first_variant,
                    submission_key=submission_key,
                )
            category, unit = _locked_material_dependencies(
                workshop,
                category_id=category_id,
                category_version=category_version,
                unit_id=unit_id,
                unit_version=unit_version,
            )
            material = Material.objects.create(
                workshop=workshop, name=name, category=category, unit=unit
            )
            variant = effect = None
            if first_variant is not None:
                variant = MaterialVariant.objects.create(
                    workshop=workshop,
                    material=material,
                    spec_label=first_variant["spec_label"],
                    min_threshold=first_variant["min_threshold"],
                )
                effect = _opening_effect(
                    workshop=workshop,
                    actor=actor,
                    variant=variant,
                    quantity=first_variant["opening_quantity"],
                    submission_key=submission_key,
                    command_type="material_create",
                )
            _material_event(actor, material, "CREATED", ("name", "category", "unit"))
            if variant is not None:
                _variant_event(
                    actor, variant, "CREATED", ("spec_label", "min_threshold")
                )
                if variant.current_stock > 0:
                    _replenishment_event(actor, variant, effect)
                summary = {
                    "material_id": material.id,
                    "material_version": material.version,
                    "first_variant_id": variant.id,
                    "first_variant_version": variant.version,
                    "opening_effect_id": effect.id,
                }
            else:
                summary = {
                    "material_id": material.id,
                    "material_version": material.version,
                }
            ConfigurationCommandReceipt.objects.create(
                workshop=workshop,
                actor_user=actor,
                command_type="material_create",
                submission_key=submission_key,
                fingerprint_version=1,
                payload_fingerprint=fingerprint,
                result_type="material",
                result_id=material.id,
                result_summary=summary,
            )
            return MaterialCommandResult(
                "committed",
                material.id,
                material.version,
                variant.id if variant else None,
                variant.version if variant else None,
                effect.id if effect else None,
            )
    except IntegrityError, TypeError, ValueError, InvalidOperation:
        return MaterialCommandResult("invalid")


def _recover_variant_create(
    receipt,
    workshop,
    actor,
    fingerprint,
    *,
    material_id,
    opening_quantity,
    submission_key,
):
    if (
        receipt.actor_user_id != actor.id
        or receipt.fingerprint_version != 1
        or receipt.payload_fingerprint != fingerprint
        or receipt.result_type != "material_variant"
        or not isinstance(receipt.result_summary, dict)
        or set(receipt.result_summary)
        != {"material_variant_id", "material_variant_version", "opening_effect_id"}
    ):
        return MaterialCommandResult("unavailable")
    summary = receipt.result_summary
    variant = MaterialVariant.objects.filter(
        pk=summary.get("material_variant_id"),
        workshop=workshop,
        material_id=material_id,
    ).first()
    effect = StockEffect.objects.filter(pk=summary.get("opening_effect_id")).first()
    if (
        variant is None
        or receipt.result_id != variant.id
        or summary.get("material_variant_version") != 2
        or not _opening_effect_is_exact(
            effect=effect,
            workshop=workshop,
            actor=actor,
            variant=variant,
            opening_quantity=opening_quantity,
            submission_key=submission_key,
            command_type="material_variant_create",
        )
    ):
        return MaterialCommandResult("unavailable")
    return MaterialCommandResult(
        "recovered", variant.material_id, None, variant.id, 2, effect.id
    )


def create_material_variant(
    *, actor_id, workshop_id, material_id, submission_key, data
):
    if not submission_key or not isinstance(data, dict):
        return MaterialCommandResult("unavailable")
    try:
        material_id = _positive_integer(material_id)
        parent_version = _positive_integer(data.get("material_version"))
        spec_label = _material_text(data.get("spec_label"), "Variant label")
        opening = _material_decimal(data.get("opening_quantity"), "Opening quantity")
        threshold = _material_decimal(data.get("min_threshold"), "Minimum threshold")
        normalized = {
            "material_id": material_id,
            "material_version": parent_version,
            "spec_label": spec_label,
            "opening_quantity": str(opening),
            "min_threshold": str(threshold),
        }
        fingerprint = _material_fingerprint(normalized)
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return MaterialCommandResult("unavailable")
            receipt = ConfigurationCommandReceipt.objects.filter(
                workshop=workshop,
                command_type="material_variant_create",
                submission_key=submission_key,
            ).first()
            if receipt is not None:
                return _recover_variant_create(
                    receipt,
                    workshop,
                    actor,
                    fingerprint,
                    material_id=material_id,
                    opening_quantity=opening,
                    submission_key=submission_key,
                )
            material = (
                Material.objects.select_for_update(no_key=True)
                .filter(pk=material_id, workshop=workshop)
                .first()
            )
            if material is None or material.status != Material.Status.ACTIVE:
                return MaterialCommandResult("unavailable")
            if material.version != parent_version:
                return MaterialCommandResult("stale", material.id, material.version)
            variant = MaterialVariant.objects.create(
                workshop=workshop,
                material=material,
                spec_label=spec_label,
                min_threshold=threshold,
            )
            effect = _opening_effect(
                workshop=workshop,
                actor=actor,
                variant=variant,
                quantity=opening,
                submission_key=submission_key,
                command_type="material_variant_create",
            )
            _variant_event(actor, variant, "CREATED", ("spec_label", "min_threshold"))
            if opening > 0:
                _replenishment_event(actor, variant, effect)
            summary = {
                "material_variant_id": variant.id,
                "material_variant_version": variant.version,
                "opening_effect_id": effect.id,
            }
            ConfigurationCommandReceipt.objects.create(
                workshop=workshop,
                actor_user=actor,
                command_type="material_variant_create",
                submission_key=submission_key,
                fingerprint_version=1,
                payload_fingerprint=fingerprint,
                result_type="material_variant",
                result_id=variant.id,
                result_summary=summary,
            )
            return MaterialCommandResult(
                "committed", material.id, material.version, variant.id, 2, effect.id
            )
    except IntegrityError, TypeError, ValueError, InvalidOperation:
        return MaterialCommandResult("invalid")


def _material_receipt_recovery(
    *, workshop, actor, target_type, target_id, command_family, key, fingerprint
):
    receipt = MaterialCommandReceipt.objects.filter(
        workshop=workshop, actor_user=actor, idempotency_key=key
    ).first()
    if receipt is None:
        return None
    model = Material if target_type == "material" else MaterialVariant
    source = model.objects.filter(pk=target_id, workshop=workshop).first()
    expected_summary_keys = {
        f"{target_type}_id",
        f"{target_type}_version",
    }
    valid = (
        receipt.target_type == target_type
        and receipt.target_id == target_id
        and receipt.command_family == command_family
        and receipt.request_fingerprint == fingerprint
        and isinstance(receipt.result_summary, dict)
        and set(receipt.result_summary) == expected_summary_keys
        and receipt.result_summary[f"{target_type}_id"] == target_id
        and receipt.result_summary[f"{target_type}_version"] == receipt.result_version
        and source is not None
    )
    if not valid:
        return MaterialCommandResult("unavailable")
    if target_type == "material":
        return MaterialCommandResult("recovered", source.id, receipt.result_version)
    return MaterialCommandResult(
        "recovered",
        source.material_id,
        None,
        source.id,
        receipt.result_version,
    )


def _write_material_receipt(
    *, workshop, actor, target_type, source, command_family, key, fingerprint
):
    MaterialCommandReceipt.objects.create(
        workshop=workshop,
        actor_user=actor,
        target_type=target_type,
        target_id=source.id,
        idempotency_key=key,
        command_family=command_family,
        request_fingerprint=fingerprint,
        result_version=source.version,
        result_summary={
            f"{target_type}_id": source.id,
            f"{target_type}_version": source.version,
        },
    )


def edit_material(
    *, actor_id, workshop_id, material_id, expected_version, idempotency_key, data
):
    if not idempotency_key or not isinstance(data, dict):
        return MaterialCommandResult("unavailable")
    try:
        material_id = _positive_integer(material_id)
        expected_version = _positive_integer(expected_version)
        name = _material_text(data.get("name"), "Name")
        category_id = _positive_integer(data.get("category_id"))
        category_version = _positive_integer(data.get("category_version"))
        unit_id = _positive_integer(data.get("unit_id"))
        unit_version = _positive_integer(data.get("unit_version"))
        normalized = {
            "target_type": "material",
            "target_id": material_id,
            "command_family": "edit",
            "expected_version": expected_version,
            "name": name,
            "category_id": category_id,
            "category_version": category_version,
            "unit_id": unit_id,
            "unit_version": unit_version,
        }
        fingerprint = _material_fingerprint(normalized)
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return MaterialCommandResult("unavailable")
            recovered = _material_receipt_recovery(
                workshop=workshop,
                actor=actor,
                target_type="material",
                target_id=material_id,
                command_family="edit",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            if recovered is not None:
                return recovered
            category, unit = _locked_material_dependencies(
                workshop,
                category_id=category_id,
                category_version=category_version,
                unit_id=unit_id,
                unit_version=unit_version,
            )
            material = (
                Material.objects.select_for_update()
                .filter(pk=material_id, workshop=workshop)
                .first()
            )
            if material is None:
                return MaterialCommandResult("unavailable")
            if material.version != expected_version:
                return MaterialCommandResult("stale", material.id, material.version)
            if unit.id != material.unit_id and material.variants.exists():
                return MaterialCommandResult("blocked")
            changed = []
            for field_name, value in (
                ("name", name),
                ("category", category),
                ("unit", unit),
            ):
                current = (
                    getattr(material, f"{field_name}_id", None)
                    if field_name in {"category", "unit"}
                    else getattr(material, field_name)
                )
                proposed = value.id if field_name in {"category", "unit"} else value
                if current != proposed:
                    setattr(material, field_name, value)
                    changed.append(field_name)
            if not changed:
                return MaterialCommandResult("committed", material.id, material.version)
            material.version += 1
            material.save(update_fields=[*changed, "version"])
            _material_event(actor, material, "UPDATED", changed)
            _write_material_receipt(
                workshop=workshop,
                actor=actor,
                target_type="material",
                source=material,
                command_family="edit",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            return MaterialCommandResult("committed", material.id, material.version)
    except IntegrityError, TypeError, ValueError:
        return MaterialCommandResult("invalid")


def edit_material_variant(
    *, actor_id, workshop_id, variant_id, expected_version, idempotency_key, data
):
    if not idempotency_key or not isinstance(data, dict):
        return MaterialCommandResult("unavailable")
    try:
        variant_id = _positive_integer(variant_id)
        expected_version = _positive_integer(expected_version)
        spec_label = _material_text(data.get("spec_label"), "Variant label")
        threshold = _material_decimal(data.get("min_threshold"), "Minimum threshold")
        normalized = {
            "target_type": "material_variant",
            "target_id": variant_id,
            "command_family": "edit",
            "expected_version": expected_version,
            "spec_label": spec_label,
            "min_threshold": str(threshold),
        }
        fingerprint = _material_fingerprint(normalized)
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return MaterialCommandResult("unavailable")
            recovered = _material_receipt_recovery(
                workshop=workshop,
                actor=actor,
                target_type="material_variant",
                target_id=variant_id,
                command_family="edit",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            if recovered is not None:
                return recovered
            variant = (
                MaterialVariant.objects.select_for_update()
                .select_related("material")
                .filter(pk=variant_id, workshop=workshop)
                .first()
            )
            if variant is None:
                return MaterialCommandResult("unavailable")
            Material.objects.select_for_update().get(pk=variant.material_id)
            if variant.version != expected_version:
                return MaterialCommandResult(
                    "stale", variant.material_id, None, variant.id, variant.version
                )
            changed = []
            if variant.spec_label != spec_label:
                variant.spec_label = spec_label
                changed.append("spec_label")
            if variant.min_threshold != threshold:
                variant.min_threshold = threshold
                changed.append("min_threshold")
            if not changed:
                return MaterialCommandResult(
                    "committed", variant.material_id, None, variant.id, variant.version
                )
            variant.version += 1
            variant.save(update_fields=[*changed, "version"])
            _variant_event(actor, variant, "UPDATED", changed)
            _write_material_receipt(
                workshop=workshop,
                actor=actor,
                target_type="material_variant",
                source=variant,
                command_family="edit",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            return MaterialCommandResult(
                "committed", variant.material_id, None, variant.id, variant.version
            )
    except IntegrityError, TypeError, ValueError, InvalidOperation:
        return MaterialCommandResult("invalid")


def _transition_material_target(
    *,
    actor_id,
    workshop_id,
    target_type,
    target_id,
    expected_version,
    idempotency_key,
    action,
):
    if action not in {"archive", "restore"} or not idempotency_key:
        return MaterialCommandResult("unavailable")
    try:
        target_id = _positive_integer(target_id)
        expected_version = _positive_integer(expected_version)
        fingerprint = _material_fingerprint(
            {
                "target_type": target_type,
                "target_id": target_id,
                "command_family": action,
                "expected_version": expected_version,
            }
        )
        with transaction.atomic():
            actor, workshop = _locked_admin(actor_id, workshop_id)
            if actor is None:
                return MaterialCommandResult("unavailable")
            recovered = _material_receipt_recovery(
                workshop=workshop,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                command_family=action,
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            if recovered is not None:
                return recovered
            model = Material if target_type == "material" else MaterialVariant
            source = (
                model.objects.select_for_update()
                .filter(pk=target_id, workshop=workshop)
                .first()
            )
            if source is None:
                return MaterialCommandResult("unavailable")
            if source.version != expected_version:
                if target_type == "material":
                    return MaterialCommandResult("stale", source.id, source.version)
                return MaterialCommandResult(
                    "stale", source.material_id, None, source.id, source.version
                )
            target_status = "archived" if action == "archive" else "active"
            if source.status == target_status:
                if target_type == "material":
                    return MaterialCommandResult("committed", source.id, source.version)
                return MaterialCommandResult(
                    "committed", source.material_id, None, source.id, source.version
                )
            if target_type == "material":
                MaterialCategory.objects.select_for_update().get(pk=source.category_id)
                UnitType.objects.select_for_update().get(pk=source.unit_id)
                if (
                    action == "archive"
                    and source.variants.filter(status="active").exists()
                ):
                    return MaterialCommandResult("blocked")
                if action == "restore":
                    dependency_valid = (
                        source.unit.status == UnitType.Status.ACTIVE
                        and source.category.status == MaterialCategory.Status.ACTIVE
                    )
                    if not dependency_valid:
                        return MaterialCommandResult("blocked")
            else:
                parent = Material.objects.select_for_update().get(pk=source.material_id)
                if action == "restore" and parent.status != Material.Status.ACTIVE:
                    return MaterialCommandResult("blocked")
            source.status = target_status
            source.version += 1
            source.save(update_fields=("status", "version"))
            event_action = "RETIRED" if action == "archive" else "RESTORED"
            if target_type == "material":
                _material_event(actor, source, event_action, ("status",))
            else:
                _variant_event(actor, source, event_action, ("status",))
            _write_material_receipt(
                workshop=workshop,
                actor=actor,
                target_type=target_type,
                source=source,
                command_family=action,
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            if target_type == "material":
                return MaterialCommandResult("committed", source.id, source.version)
            return MaterialCommandResult(
                "committed", source.material_id, None, source.id, source.version
            )
    except IntegrityError, TypeError, ValueError:
        return MaterialCommandResult("invalid")


def transition_material(
    *, actor_id, workshop_id, material_id, expected_version, idempotency_key, action
):
    return _transition_material_target(
        actor_id=actor_id,
        workshop_id=workshop_id,
        target_type="material",
        target_id=material_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        action=action,
    )


def transition_material_variant(
    *, actor_id, workshop_id, variant_id, expected_version, idempotency_key, action
):
    return _transition_material_target(
        actor_id=actor_id,
        workshop_id=workshop_id,
        target_type="material_variant",
        target_id=variant_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        action=action,
    )
