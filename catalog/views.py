"""Library / Station / Material import + read-only table views (C1, C2).

Every view scopes its queries to ``request.user.workshop`` (D-126) — the
setup-gate (A2) guarantees an authenticated user has one. Import is admin-only
(it writes); the read-only tables are available to any authenticated workshop
member (managers/operators/technicians get read access — the finer per-role
split and manual CRUD are later slices).

HTMX-first: the ``*_table`` views return a table fragment for the card/tab's
lazy-load and for every search / sort / filter / paginate interaction, all of
which compose through one query string. The ``*_import`` views run the engine
and return the refreshed fragment with a summary toast on top.

C1 covers the five dependency-free library types through the generic
``library_table``/``library_import`` pair (WorkshopRole joins them in C2 as a
sixth card). Station and Material get dedicated views: Station has two combinable
filters, and Material renders a grouped, paginated-by-Material table.
"""

from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_POST

from accounts.models import User
from catalog.library_config import Column, get_library_type
from catalog.models import (
    Material,
    MaterialCategory,
    MaterialVariant,
    Station,
    StationCategory,
    UnitType,
)
from catalog.services import import_library, import_materials


def _get_lib(slug):
    try:
        return get_library_type(slug)
    except KeyError as exc:
        raise Http404("Unknown library type") from exc


def _require_admin(request):
    if request.user.account_role != User.AccountRole.ADMIN:
        raise PermissionDenied("Only an admin can import.")


def _sort_direction(request):
    direction = request.GET.get("dir", "asc")
    return direction if direction in ("asc", "desc") else "asc"


def _header_columns(columns, sort, direction):
    """Decorate a column list with the current sort state for the header row."""
    result = []
    for column in columns:
        is_sorted = column.sortable and column.key == sort
        result.append(
            {
                "key": column.key,
                "label": column.label,
                "kind": column.kind,
                "sortable": column.sortable,
                "next_dir": "desc" if (is_sorted and direction == "asc") else "asc",
                "indicator": ("▲" if direction == "asc" else "▼") if is_sorted else "",
            }
        )
    return result


# --------------------------------------------------------------------------- #
# Generic library tables (C1 five types + WorkshopRole)
# --------------------------------------------------------------------------- #


def _table_context(request, lib):
    qs = lib.model.objects.filter(workshop=request.user.workshop)

    search = request.GET.get("search", "").strip()
    if search:
        qs = qs.filter(name__icontains=search)

    filter_value = ""
    if lib.filter:
        filter_value = request.GET.get(lib.filter.param, "")
        condition = lib.filter.resolve(filter_value)
        if condition is not None:
            qs = qs.filter(condition)

    # Whitelist the sort field — never order_by() a raw query param.
    sort = request.GET.get("sort", "name")
    if sort not in lib.sortable_keys:
        sort = "name"
    direction = _sort_direction(request)
    qs = qs.order_by(("-" if direction == "desc" else "") + sort, "pk")

    page_obj = Paginator(qs, lib.page_size).get_page(request.GET.get("page"))

    return {
        "lib": lib,
        "page_obj": page_obj,
        "columns": _header_columns(lib.columns, sort, direction),
        "search": search,
        "sort": sort,
        "dir": direction,
        "filter_value": filter_value,
    }


@login_required
def library_table(request, slug):
    lib = _get_lib(slug)
    return render(request, "catalog/_library_table.html", _table_context(request, lib))


@require_POST
@login_required
def library_import(request, slug):
    lib = _get_lib(slug)
    _require_admin(request)
    summary = import_library(slug, request.user.workshop)
    context = _table_context(request, lib)
    context["summary"] = summary
    return render(request, "catalog/_library_table.html", context)


# --------------------------------------------------------------------------- #
# Stations tab (dedicated view: two combinable filters + list column)
# --------------------------------------------------------------------------- #

STATION_COLUMNS = (
    Column("code", "ID"),
    Column("name", "Name"),
    Column("category", "Category", kind="ref"),
    Column("supported_operations", "Supported operations", kind="list", sortable=False),
    Column("status", "Status", kind="status"),
)
# Whitelisted sort keys → the ORM field they order by.
_STATION_SORTS = {"code": "code", "name": "name", "category": "category__name", "status": "status"}
STATION_PAGE_SIZE = 50


def _station_context(request):
    workshop = request.user.workshop
    qs = (
        Station.objects.filter(workshop=workshop)
        .select_related("category")
        .prefetch_related("supported_operations")
    )

    search = request.GET.get("search", "").strip()
    if search:
        qs = qs.filter(name__icontains=search)

    category = request.GET.get("category", "")
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    else:
        category = ""

    status = request.GET.get("status", "")
    if status in Station.Status.values:
        qs = qs.filter(status=status)
    else:
        status = ""

    sort = request.GET.get("sort", "code")
    if sort not in _STATION_SORTS:
        sort = "code"
    direction = _sort_direction(request)
    qs = qs.order_by(("-" if direction == "desc" else "") + _STATION_SORTS[sort], "pk")

    page_obj = Paginator(qs, STATION_PAGE_SIZE).get_page(request.GET.get("page"))

    return {
        "page_obj": page_obj,
        "columns": _header_columns(STATION_COLUMNS, sort, direction),
        "search": search,
        "sort": sort,
        "dir": direction,
        "category_filter": category,
        "status_filter": status,
        "categories": StationCategory.objects.filter(workshop=workshop).order_by("name"),
        "statuses": Station.Status.choices,
    }


@login_required
def station_table(request):
    return render(request, "catalog/_station_table.html", _station_context(request))


@require_POST
@login_required
def station_import(request):
    _require_admin(request)
    summary = import_library("station", request.user.workshop)
    context = _station_context(request)
    context["summary"] = summary
    return render(request, "catalog/_station_table.html", context)


# --------------------------------------------------------------------------- #
# Materials tab (dedicated view: grouped display, paginated by Material)
# --------------------------------------------------------------------------- #

MATERIAL_PAGE_SIZE = 50


def _material_context(request):
    workshop = request.user.workshop
    qs = Material.objects.filter(workshop=workshop).select_related("category", "unit")

    search = request.GET.get("search", "").strip()
    if search:
        # A variant-level spec_label match still returns its parent Material.
        qs = qs.filter(
            Q(name__icontains=search) | Q(variants__spec_label__icontains=search)
        ).distinct()

    category = request.GET.get("category", "")
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    else:
        category = ""

    unit = request.GET.get("unit", "")
    if unit.isdigit():
        qs = qs.filter(unit_id=int(unit))
    else:
        unit = ""

    # Low-stock is a variant-level filter: current_stock < min_threshold, no
    # reserved/available involved (reserved is only ever set by production Orders,
    # Phase 2+). A Material with no matching variant drops out of the view.
    low = request.GET.get("low_stock", "") == "1"
    if low:
        qs = qs.filter(variants__current_stock__lt=F("variants__min_threshold")).distinct()

    # Only the Material name is sortable (variant order is insertion order).
    direction = _sort_direction(request)
    qs = qs.order_by(("-" if direction == "desc" else "") + "name", "pk")

    page_obj = Paginator(qs, MATERIAL_PAGE_SIZE).get_page(request.GET.get("page"))

    # Group variants under their Material for the current page (never split across a
    # page boundary). The Low-stock filter also narrows which variants display.
    materials = list(page_obj)
    variant_qs = MaterialVariant.objects.filter(material__in=materials)
    if low:
        variant_qs = variant_qs.filter(current_stock__lt=F("min_threshold"))
    grouped = defaultdict(list)
    for variant in variant_qs.order_by("pk"):
        grouped[variant.material_id].append(variant)
    rows = [{"material": m, "variants": grouped.get(m.pk, [])} for m in materials]

    return {
        "page_obj": page_obj,
        "rows": rows,
        "search": search,
        "dir": direction,
        "category_filter": category,
        "unit_filter": unit,
        "low_stock": "1" if low else "",
        "categories": MaterialCategory.objects.filter(workshop=workshop).order_by("name"),
        "units": UnitType.objects.filter(workshop=workshop).order_by("name"),
    }


@login_required
def material_table(request):
    return render(request, "catalog/_material_table.html", _material_context(request))


@require_POST
@login_required
def material_import(request):
    _require_admin(request)
    summary = import_materials(request.user.workshop)
    context = _material_context(request)
    context["summary"] = summary
    return render(request, "catalog/_material_table.html", context)
