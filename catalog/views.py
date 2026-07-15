"""Library import + read-only table views (C1).

Both views scope every query to ``request.user.workshop`` (D-126) — the
setup-gate (A2) guarantees an authenticated user has one. Import is admin-only
(it writes); the read-only table is available to any authenticated workshop
member (managers/operators/technicians get read access to workshop data — the
finer per-role split and manual CRUD are later slices).

HTMX-first: ``library_table`` returns the ``_library_table`` fragment for the
card's lazy-load and for every search / sort / filter / paginate interaction,
all of which compose through one query string. ``library_import`` runs the
engine and returns the refreshed fragment with a summary toast on top.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_POST

from accounts.models import User
from catalog.library_config import get_library_type
from catalog.services import import_library

PAGE_SIZE = 20


def _get_lib(slug):
    try:
        return get_library_type(slug)
    except KeyError as exc:
        raise Http404("Unknown library type") from exc


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
    direction = request.GET.get("dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"
    qs = qs.order_by(("-" if direction == "desc" else "") + sort, "pk")

    page_obj = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))

    columns = []
    for column in lib.columns:
        is_sorted = column.sortable and column.key == sort
        columns.append(
            {
                "key": column.key,
                "label": column.label,
                "kind": column.kind,
                "sortable": column.sortable,
                "next_dir": "desc" if (is_sorted and direction == "asc") else "asc",
                "indicator": ("▲" if direction == "asc" else "▼") if is_sorted else "",
            }
        )

    return {
        "lib": lib,
        "page_obj": page_obj,
        "columns": columns,
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
    if request.user.account_role != User.AccountRole.ADMIN:
        raise PermissionDenied("Only an admin can import a library.")
    summary = import_library(slug, request.user.workshop)
    context = _table_context(request, lib)
    context["summary"] = summary
    return render(request, "catalog/_library_table.html", context)
