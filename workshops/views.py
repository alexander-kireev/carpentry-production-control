import uuid

from django.db import models
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from identity.queries import resolve_authenticated_destination

from .commands import (
    FAMILY_MODELS,
    create_library_item,
    edit_library_item,
    transition_library_item,
)
from .forms import FORM_CLASSES
from .models import OperationType
from .queries import FAMILY_LABELS, get_libraries_catalogue, resolve_libraries_access


def _denied(request):
    destination = resolve_authenticated_destination(request.user)
    if destination.supported:
        return redirect(str(destination.destination))
    raise Http404


def _clearance_choices(workshop_id):
    rows = (
        OperationType.objects.filter(status="active")
        .filter(
            models.Q(workshop_id=workshop_id)
            | models.Q(workshop__isnull=True, machine_key="other")
        )
        .order_by("name", "id")
    )
    return tuple((row.id, row.name) for row in rows)


def _forms(workshop_id, bound_family=None, data=None):
    result = {}
    for family, form_class in FORM_CLASSES.items():
        kwargs = {"initial": {"submission_key": uuid.uuid4()}}
        if family == bound_family:
            kwargs = {"data": data}
        if family == "workshop_role":
            kwargs["clearance_choices"] = _clearance_choices(workshop_id)
        result[family] = form_class(**kwargs)
    return result


def _edit_form(workshop_id, family, item_id, *, data=None):
    source = (
        FAMILY_MODELS[family]
        .objects.filter(pk=item_id, workshop_id=workshop_id)
        .first()
    )
    if source is None or getattr(source, "machine_key", None) is not None:
        return None
    initial = {
        key: getattr(source, key)
        for key in FORM_CLASSES[family].base_fields
        if key != "submission_key" and hasattr(source, key)
    }
    if family == "workshop_role":
        initial["default_clearance_ids"] = list(
            source.default_clearance_links.values_list("operation_type_id", flat=True)
        )
    kwargs = {
        "edit": True,
        "prefix": f"edit-{family}-{item_id}",
        "initial": initial,
    }
    if data is not None:
        kwargs["data"] = data
    if family == "workshop_role":
        kwargs["clearance_choices"] = _clearance_choices(workshop_id)
    return FORM_CLASSES[family](**kwargs)


def _render(
    request,
    access,
    *,
    forms=None,
    result=None,
    status_code=200,
    bound_edit=None,
    selected_family=None,
):
    if selected_family not in FAMILY_LABELS:
        selected_family = request.GET.get("family")
    if selected_family not in FAMILY_LABELS:
        selected_family = "workshop_role"
    catalogue = get_libraries_catalogue(
        access.actor,
        family=selected_family,
        status=request.GET.get("status"),
        search=request.GET.get("q", ""),
    )
    if catalogue is not None and access.mode == "admin":
        for family in catalogue["families"]:
            for row in family["rows"]:
                if row["can_edit"]:
                    if bound_edit and bound_edit[:2] == (family["key"], row["id"]):
                        row["edit_form"] = bound_edit[2]
                    else:
                        row["edit_form"] = _edit_form(
                            access.workshop.id, family["key"], row["id"]
                        )
    render_forms = forms or _forms(access.workshop.id)
    context = {
        **catalogue,
        "identity_user": access.actor,
        "family_labels": FAMILY_LABELS,
        "forms": render_forms,
        "auto_open_create": next(
            (family for family, form in render_forms.items() if form.errors), None
        ),
        "result": result,
    }
    template = (
        "workshops/libraries_admin.html"
        if access.mode == "admin"
        else "workshops/libraries_manager.html"
    )
    return render(request, template, context, status=status_code)


@require_GET
def libraries(request):
    access = resolve_libraries_access(request.user)
    if access is None:
        return _denied(request)
    return _render(request, access)


@require_POST
def library_create(request, family):
    access = resolve_libraries_access(request.user)
    if access is None or access.mode != "admin" or family not in FORM_CLASSES:
        return _denied(request)
    forms = _forms(access.workshop.id, family, request.POST)
    form = forms[family]
    if not form.is_valid():
        return _render(
            request,
            access,
            forms=forms,
            result="validation_error",
            status_code=400,
            selected_family=family,
        )
    data = dict(form.cleaned_data)
    submission_key = str(data.pop("submission_key"))
    if family == "workshop_role":
        selected = list(
            OperationType.objects.filter(pk__in=data["default_clearance_ids"])
        )
        data["default_clearance_versions"] = {row.id: row.version for row in selected}
    result = create_library_item(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        family=family,
        submission_key=submission_key,
        data=data,
    )
    return _render(
        request,
        access,
        result=result.code,
        status_code=400 if result.code == "validation_error" else 200,
        selected_family=family,
    )


@require_POST
def library_edit(request, family, item_id):
    access = resolve_libraries_access(request.user)
    if access is None or access.mode != "admin" or family not in FORM_CLASSES:
        return _denied(request)
    form = _edit_form(access.workshop.id, family, item_id, data=request.POST)
    if form is None:
        return _render(
            request,
            access,
            result="unavailable",
            status_code=400,
            selected_family=family,
        )
    try:
        expected_version = int(request.POST.get("version", ""))
    except ValueError:
        return _render(
            request,
            access,
            result="unavailable",
            status_code=400,
            selected_family=family,
        )
    if not form.is_valid():
        return _render(
            request,
            access,
            result="validation_error",
            status_code=400,
            bound_edit=(family, item_id, form),
            selected_family=family,
        )
    data = dict(form.cleaned_data)
    if family == "workshop_role":
        selected = list(
            OperationType.objects.filter(pk__in=data["default_clearance_ids"])
        )
        data["default_clearance_versions"] = {row.id: row.version for row in selected}
    result = edit_library_item(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        family=family,
        item_id=item_id,
        expected_version=expected_version,
        data=data,
    )
    return _render(request, access, result=result.code, selected_family=family)


@require_POST
def library_transition(request, family, item_id, action):
    access = resolve_libraries_access(request.user)
    if access is None or access.mode != "admin" or family not in FORM_CLASSES:
        return _denied(request)
    try:
        expected_version = int(request.POST.get("version", ""))
    except ValueError:
        return _render(
            request,
            access,
            result="unavailable",
            status_code=400,
            selected_family=family,
        )
    result = transition_library_item(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        family=family,
        item_id=item_id,
        expected_version=expected_version,
        action=action,
    )
    return _render(request, access, result=result.code, selected_family=family)
