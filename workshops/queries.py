from dataclasses import dataclass

from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models.functions import Lower

from identity.models import User

from .models import (
    Material,
    MaterialCategory,
    MaterialVariant,
    OperationType,
    ShiftDefinition,
    Station,
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


@dataclass(frozen=True)
class MaterialsAccess:
    actor: User
    workshop: Workshop
    mode: str
    pending_setup: bool


@dataclass(frozen=True)
class StationsAccess:
    actor: User
    workshop: Workshop
    mode: str
    pending_setup: bool


def resolve_stations_access(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        actor = User.objects.select_related("workshop", "workshop_role").get(pk=user.pk)
        resolve_protected_configuration()
        verify_workshop_protected_pair(actor.workshop)
    except User.DoesNotExist, Workshop.DoesNotExist, ProtectedConfigurationError:
        return None
    role = actor.workshop_role
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
    exact_member = (
        actor.account_role in {User.AccountRole.MANAGER, User.AccountRole.OPERATOR}
        and role.machine_key != "admin"
        and role.workshop_id in {None, actor.workshop_id}
    )
    if exact_admin and actor.workshop.status in {
        Workshop.Status.MANAGER_ACTIVATION_PENDING,
        Workshop.Status.OPERATIONAL,
    }:
        return StationsAccess(
            actor,
            actor.workshop,
            "admin",
            actor.workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING,
        )
    if exact_member and actor.workshop.status == Workshop.Status.OPERATIONAL:
        return StationsAccess(actor, actor.workshop, actor.account_role, False)
    return None


def _station_capability_options(workshop):
    return (
        OperationType.objects.filter(
            status=OperationType.Status.ACTIVE, is_production=True
        )
        .filter(
            Q(workshop=workshop)
            | Q(workshop__isnull=True, machine_key="other", name="Other")
        )
        .order_by(Lower("name"), "id")
    )


def _project_station(access, station):
    row = {
        "code": station.code,
        "name": station.name,
        "lifecycle": station.lifecycle_status,
        "availability": station.availability_status,
        "capabilities": [
            {"id": link.operation_type_id, "label": link.operation_type.name}
            for link in station.supported_operation_links.all()
        ],
    }
    if access.mode == "admin":
        row.update(
            id=station.id,
            version=station.version,
            can_edit=station.lifecycle_status == Station.LifecycleStatus.ACTIVE,
            can_retire=station.lifecycle_status == Station.LifecycleStatus.ACTIVE,
        )
    return row


def get_stations_catalogue(
    user,
    *,
    search="",
    lifecycle=None,
    availability=None,
    capability_id=None,
    page=None,
    page_size=None,
):
    access = resolve_stations_access(user)
    if access is None:
        return None
    safe_search = search.strip()
    safe_lifecycle = lifecycle if lifecycle in {"active", "retired"} else ""
    safe_availability = (
        availability if availability in {"available", "offline", "broken"} else ""
    )
    try:
        safe_page_size = int(page_size)
    except TypeError, ValueError:
        safe_page_size = 20
    if safe_page_size not in {20, 50, 100}:
        safe_page_size = 20
    options = list(_station_capability_options(access.workshop))
    option_ids = {row.id for row in options}
    try:
        safe_capability_id = int(capability_id) if capability_id else None
    except TypeError, ValueError:
        safe_capability_id = None
    if safe_capability_id not in option_ids:
        safe_capability_id = None
    rows = (
        Station.objects.filter(workshop=access.workshop)
        .prefetch_related("supported_operation_links__operation_type")
        .order_by("code", "id")
    )
    if safe_search:
        rows = rows.filter(
            Q(code__icontains=safe_search)
            | Q(name__icontains=safe_search)
            | Q(supported_operation_links__operation_type__name__icontains=safe_search)
        ).distinct()
    if safe_lifecycle:
        rows = rows.filter(lifecycle_status=safe_lifecycle)
    if safe_availability:
        rows = rows.filter(availability_status=safe_availability)
    if safe_capability_id:
        rows = rows.filter(
            supported_operation_links__operation_type_id=safe_capability_id
        )
    paginator = Paginator(rows, safe_page_size)
    try:
        safe_page = int(page)
    except TypeError, ValueError:
        safe_page = 1
    page_obj = paginator.get_page(safe_page if safe_page > 0 else 1)
    return {
        "mode": access.mode,
        "pending_setup": access.pending_setup,
        "workshop_name": access.workshop.name,
        "stations": [_project_station(access, row) for row in page_obj.object_list],
        "capability_options": [{"id": row.id, "label": row.name} for row in options],
        "page_obj": page_obj,
        "filters": {
            "q": safe_search,
            "lifecycle": safe_lifecycle,
            "availability": safe_availability,
            "capability": safe_capability_id or "",
            "page_size": safe_page_size,
        },
    }


def get_station_detail(user, station_code):
    access = resolve_stations_access(user)
    if access is None:
        return None
    station = (
        Station.objects.filter(workshop=access.workshop, code=station_code)
        .prefetch_related("supported_operation_links__operation_type")
        .first()
    )
    if station is None:
        return None
    return {
        "mode": access.mode,
        "pending_setup": access.pending_setup,
        "workshop_name": access.workshop.name,
        "station": _project_station(access, station),
        "capability_options": [
            {"id": row.id, "label": row.name}
            for row in _station_capability_options(access.workshop)
        ],
    }


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


def resolve_materials_access(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        actor = User.objects.select_related("workshop", "workshop_role").get(pk=user.pk)
    except User.DoesNotExist:
        return None
    try:
        resolve_protected_configuration()
        verify_workshop_protected_pair(actor.workshop)
    except ProtectedConfigurationError, Workshop.DoesNotExist:
        return None
    role = actor.workshop_role
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
    exact_member = (
        actor.account_role in {User.AccountRole.MANAGER, User.AccountRole.OPERATOR}
        and role.machine_key != "admin"
        and role.workshop_id in {None, actor.workshop_id}
    )
    if exact_admin and actor.workshop.status in {
        Workshop.Status.MANAGER_ACTIVATION_PENDING,
        Workshop.Status.OPERATIONAL,
    }:
        return MaterialsAccess(
            actor,
            actor.workshop,
            "admin",
            actor.workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING,
        )
    if exact_member and actor.workshop.status == Workshop.Status.OPERATIONAL:
        return MaterialsAccess(actor, actor.workshop, actor.account_role, False)
    return None


def _stock_status(current_stock, min_threshold):
    if current_stock == 0:
        return "out_of_stock"
    if current_stock <= min_threshold:
        return "low_available"
    return "healthy"


def _project_variant(access, variant):
    projected = {
        "label": variant.spec_label,
        "status": variant.status,
        "current_stock": variant.current_stock,
        "reserved": 0,
        "available": variant.current_stock,
        "reservation_shortfall": 0,
        "min_threshold": variant.min_threshold,
        "stock_status": _stock_status(variant.current_stock, variant.min_threshold),
    }
    if access.mode == "admin":
        projected.update(
            id=variant.id,
            version=variant.version,
            can_edit=True,
            can_archive=variant.status == MaterialVariant.Status.ACTIVE,
            can_restore=(
                variant.status == MaterialVariant.Status.ARCHIVED
                and variant.material.status == Material.Status.ACTIVE
            ),
        )
    return projected


def _project_material(access, material):
    projected = {
        "name": material.name,
        "category": material.category.name,
        "unit": material.unit.name,
        "unit_abbreviation": material.unit.abbreviation,
        "status": material.status,
        "variants": [_project_variant(access, row) for row in material.variants.all()],
    }
    if access.mode == "admin":
        projected.update(
            id=material.id,
            version=material.version,
            category_id=material.category_id,
            category_version=material.category.version,
            unit_id=material.unit_id,
            unit_version=material.unit.version,
            can_edit=True,
            can_add_variant=material.status == Material.Status.ACTIVE,
            can_archive=(
                material.status == Material.Status.ACTIVE
                and not any(row["status"] == "active" for row in projected["variants"])
            ),
            can_restore=(
                material.status == Material.Status.ARCHIVED
                and material.category.status == MaterialCategory.Status.ACTIVE
                and material.unit.status == UnitType.Status.ACTIVE
            ),
        )
    if access.pending_setup:
        for variant in projected["variants"]:
            for key in (
                "current_stock",
                "reserved",
                "available",
                "reservation_shortfall",
                "min_threshold",
                "stock_status",
            ):
                variant.pop(key, None)
    return projected


def get_materials_catalogue(user, *, search="", status=None, category_id=None):
    access = resolve_materials_access(user)
    if access is None:
        return None
    safe_search = search.strip()
    safe_status = status if status in {"active", "archived"} else None
    try:
        safe_category_id = int(category_id) if category_id else None
    except TypeError, ValueError:
        safe_category_id = None
    rows = (
        Material.objects.filter(workshop=access.workshop)
        .select_related("category", "unit")
        .prefetch_related("variants")
        .order_by(Lower("category__name"), Lower("name"), "id")
    )
    if safe_status:
        rows = rows.filter(status=safe_status)
    if safe_category_id:
        rows = rows.filter(category_id=safe_category_id)
    if safe_search:
        rows = rows.filter(
            Q(name__icontains=safe_search)
            | Q(category__name__icontains=safe_search)
            | Q(unit__name__icontains=safe_search)
            | Q(variants__spec_label__icontains=safe_search)
        ).distinct()
    materials = [_project_material(access, row) for row in rows]
    categories = (
        MaterialCategory.objects.filter(status="active")
        .filter(
            Q(workshop=access.workshop)
            | Q(workshop__isnull=True, machine_key="undefined", name="undefined")
        )
        .order_by(Lower("name"), "id")
    )
    units = UnitType.objects.filter(workshop=access.workshop, status="active").order_by(
        Lower("name"), "id"
    )
    selectors = {}
    if access.mode == "admin":
        selectors = {
            "categories": [
                {"id": row.id, "label": row.name, "version": row.version}
                for row in categories
            ],
            "units": [
                {
                    "id": row.id,
                    "label": row.name,
                    "abbreviation": row.abbreviation,
                    "version": row.version,
                }
                for row in units
            ],
        }
    return {
        "mode": access.mode,
        "pending_setup": access.pending_setup,
        "workshop_name": access.workshop.name,
        "materials": materials,
        "filters": {
            "q": safe_search,
            "status": safe_status or "",
            "category": safe_category_id or "",
        },
        **selectors,
    }


def get_material_detail(user, material_id):
    catalogue = get_materials_catalogue(user)
    if catalogue is None:
        return None
    access = resolve_materials_access(user)
    row = (
        Material.objects.filter(pk=material_id, workshop=access.workshop)
        .select_related("category", "unit")
        .prefetch_related("variants")
        .first()
    )
    if row is None:
        return None
    return {**catalogue, "material": _project_material(access, row)}


def get_material_variant_detail(user, variant_id):
    access = resolve_materials_access(user)
    if access is None:
        return None
    row = (
        MaterialVariant.objects.filter(pk=variant_id, workshop=access.workshop)
        .select_related("material__category", "material__unit")
        .first()
    )
    if row is None:
        return None
    return {
        "mode": access.mode,
        "pending_setup": access.pending_setup,
        "workshop_name": access.workshop.name,
        "material": _project_material(access, row.material),
        "variant": _project_variant(access, row),
    }
