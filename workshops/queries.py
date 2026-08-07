from dataclasses import dataclass

from django.db.models import Q
from django.db.models.functions import Lower

from identity.models import User

from .models import (
    MaterialCategory,
    OperationType,
    ShiftDefinition,
    UnitType,
    Workshop,
    WorkshopRole,
)
from .protected_configuration import (
    ProtectedConfigurationError,
    resolve_protected_configuration,
    verify_workshop_protected_pair,
)

FAMILY_LABELS = {
    "workshop_role": "Roles",
    "operation_type": "Operation types",
    "unit_type": "Units",
    "material_category": "Material categories",
    "shift_definition": "Shifts",
}


@dataclass(frozen=True)
class LibrariesAccess:
    actor: User
    workshop: Workshop
    mode: str


def resolve_libraries_access(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        actor = User.objects.select_related("workshop", "workshop_role").get(pk=user.pk)
    except User.DoesNotExist:
        return None
    role = actor.workshop_role
    try:
        resolve_protected_configuration()
        verify_workshop_protected_pair(actor.workshop)
    except ProtectedConfigurationError:
        return None
    if (
        actor.status != User.Status.ACTIVE
        or actor.onboarding_state is not None
        or actor.workshop_id is None
        or role is None
        or role.status != WorkshopRole.Status.ACTIVE
    ):
        return None
    exact_admin = (
        actor.account_role == User.AccountRole.ADMIN
        and role.workshop_id is None
        and role.machine_key == "admin"
        and role.name == "Admin"
    )
    exact_manager = (
        actor.account_role == User.AccountRole.MANAGER
        and role.machine_key != "admin"
        and role.workshop_id in {None, actor.workshop_id}
    )
    if exact_admin and actor.workshop.status in {
        Workshop.Status.MANAGER_ACTIVATION_PENDING,
        Workshop.Status.OPERATIONAL,
    }:
        return LibrariesAccess(actor, actor.workshop, "admin")
    if exact_manager and actor.workshop.status == Workshop.Status.OPERATIONAL:
        return LibrariesAccess(actor, actor.workshop, "manager")
    return None


def _rows(access, family):
    workshop_id = access.workshop.id
    if family == "workshop_role":
        rows = WorkshopRole.objects.filter(
            Q(workshop_id=workshop_id) | Q(workshop__isnull=True)
        ).prefetch_related("default_clearance_links__operation_type")
    elif family == "operation_type":
        rows = OperationType.objects.filter(
            Q(workshop_id=workshop_id) | Q(workshop__isnull=True)
        )
    elif family == "unit_type":
        rows = UnitType.objects.filter(workshop_id=workshop_id)
    elif family == "material_category":
        rows = MaterialCategory.objects.filter(
            Q(workshop_id=workshop_id) | Q(workshop__isnull=True)
        )
    else:
        rows = ShiftDefinition.objects.filter(workshop_id=workshop_id)
    return rows.order_by(Lower("name"), "id")


def get_libraries_catalogue(user, *, family=None, status=None, search=""):
    access = resolve_libraries_access(user)
    if access is None:
        return None
    selected = [family] if family in FAMILY_LABELS else list(FAMILY_LABELS)
    safe_status = status if status in {"active", "retired", "protected"} else None
    search_folded = search.strip().casefold()
    families = []
    for family_key in selected:
        projected = []
        for row in _rows(access, family_key):
            protected = getattr(row, "machine_key", None) is not None
            if safe_status == "protected" and not protected:
                continue
            if safe_status in {"active", "retired"} and row.status != safe_status:
                continue
            labels = [row.name]
            if family_key == "unit_type":
                labels.append(row.abbreviation)
            if search_folded and not any(
                search_folded in label.casefold() for label in labels
            ):
                continue
            item = {
                "label": row.name,
                "status": row.status,
                "protected": protected,
                "system": row.workshop_id is None,
            }
            if family_key in {"workshop_role", "operation_type"}:
                item["description"] = row.description or ""
            if family_key == "unit_type":
                item["abbreviation"] = row.abbreviation
            elif family_key == "operation_type":
                item.update(
                    is_production=row.is_production,
                    requires_clearance=row.requires_clearance,
                )
            elif family_key == "shift_definition":
                item.update(
                    start_time=row.start_time, end_time=row.end_time, days=row.days
                )
            elif family_key == "workshop_role":
                item["default_clearances"] = sorted(
                    link.operation_type.name
                    for link in row.default_clearance_links.all()
                )
            if access.mode == "admin":
                item.update(
                    id=row.id,
                    version=row.version,
                    can_edit=not protected,
                    can_retire=not protected and row.status == "active",
                    can_restore=not protected and row.status == "retired",
                )
            projected.append(item)
        families.append(
            {"key": family_key, "label": FAMILY_LABELS[family_key], "rows": projected}
        )
    return {
        "mode": access.mode,
        "workshop_name": access.workshop.name,
        "pending_setup": access.workshop.status
        == Workshop.Status.MANAGER_ACTIVATION_PENDING,
        "families": families,
        "filters": {
            "family": family or "",
            "status": safe_status or "",
            "search": search.strip(),
        },
    }
