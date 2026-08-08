import uuid
from urllib.parse import urlencode

from django.db import models
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from foundation.feedback import pop_feedback, set_feedback
from identity.queries import resolve_authenticated_destination

from .commands import (
    FAMILY_MODELS,
    create_library_item,
    create_material,
    create_material_variant,
    edit_library_item,
    edit_material,
    edit_material_variant,
    transition_library_item,
    transition_material,
    transition_material_variant,
)
from .forms import (
    FORM_CLASSES,
    MaterialForm,
    MaterialTransitionForm,
    MaterialVariantForm,
)
from .models import OperationType
from .queries import (
    FAMILY_LABELS,
    get_libraries_catalogue,
    get_material_detail,
    get_material_variant_detail,
    get_materials_catalogue,
    resolve_libraries_access,
    resolve_materials_access,
)


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
    template_name=None,
):
    fresh_access = resolve_libraries_access(request.user)
    if fresh_access is None or fresh_access.mode != access.mode:
        return _denied(request)
    access = fresh_access
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
    if catalogue is None:
        return _denied(request)
    if catalogue is not None and access.mode == "admin":
        for family in catalogue["families"]:
            for row in family["rows"]:
                if row["can_edit"]:
                    if bound_edit and bound_edit[:2] == (family["key"], row["id"]):
                        row["edit_form"] = bound_edit[2]
                        row["auto_open_edit"] = True
                    else:
                        row["edit_form"] = _edit_form(
                            access.workshop.id, family["key"], row["id"]
                        )
                        row["auto_open_edit"] = False
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
        "feedback": pop_feedback(request),
    }
    template = template_name or (
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
    result = request.session.pop("library_feedback", None)
    return _render(request, access, result=result)


def render_onboarding_setup(request):
    access = resolve_libraries_access(request.user)
    if (
        access is None
        or access.mode != "admin"
        or access.workshop.status != "manager_activation_pending"
    ):
        return _denied(request)
    return _render(
        request,
        access,
        result=request.session.pop("library_feedback", None),
        template_name="onboarding/workshop_setup.html",
    )


def _library_get_url(request, family):
    query = urlencode(
        {
            "family": family,
            "status": request.GET.get("status", ""),
            "q": request.GET.get("q", ""),
        }
    )
    return f"{reverse('workshops:libraries')}?{query}"


def _library_success_redirect(request, family, result_code):
    set_feedback(
        request,
        title="Change committed." if result_code == "success" else "Change recovered.",
        body=(
            "Workshop library truth has been refreshed."
            if result_code == "success"
            else "The previously committed result was recovered."
        ),
    )
    return redirect(_library_get_url(request, family))


def _clear_pending_library_feedback(request):
    request.session.pop("library_feedback", None)


@require_POST
def library_create(request, family):
    access = resolve_libraries_access(request.user)
    if access is None or access.mode != "admin" or family not in FORM_CLASSES:
        return _denied(request)
    _clear_pending_library_feedback(request)
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
    if result.code in {"success", "replay"}:
        return _library_success_redirect(request, family, result.code)
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
    _clear_pending_library_feedback(request)
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
    if result.code in {"success", "replay"}:
        return _library_success_redirect(request, family, result.code)
    return _render(
        request,
        access,
        result=result.code,
        status_code=400 if result.code == "validation_error" else 200,
        bound_edit=(family, item_id, form) if result.code == "stale" else None,
        selected_family=family,
    )


@require_POST
def library_transition(request, family, item_id, action):
    access = resolve_libraries_access(request.user)
    if access is None or access.mode != "admin" or family not in FORM_CLASSES:
        return _denied(request)
    _clear_pending_library_feedback(request)
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
    if result.code in {"success", "replay"}:
        return _library_success_redirect(request, family, result.code)
    return _render(request, access, result=result.code, selected_family=family)


def _material_forms(catalogue, *, bound=None, data=None):
    initial_category = (
        catalogue.get("categories", [{}])[0] if catalogue.get("categories") else {}
    )
    initial_unit = catalogue.get("units", [{}])[0] if catalogue.get("units") else {}
    kwargs = {
        "categories": catalogue.get("categories", ()),
        "units": catalogue.get("units", ()),
        "initial": {
            "submission_key": uuid.uuid4(),
            "category_id": initial_category.get("id"),
            "category_version": initial_category.get("version"),
            "unit_id": initial_unit.get("id"),
            "unit_version": initial_unit.get("version"),
        },
    }
    if bound == "create-material":
        kwargs["data"] = data
    return MaterialForm(**kwargs)


def _decorate_material_forms(catalogue, *, bound=None, data=None):
    if catalogue["mode"] != "admin":
        return
    for material in catalogue["materials"]:
        material["auto_open_edit"] = bound == f"edit-material-{material['id']}"
        material["auto_open_variant_create"] = (
            bound == f"create-variant-{material['id']}"
        )
        material["variant_create_form"] = MaterialVariantForm(
            data=data if bound == f"create-variant-{material['id']}" else None,
            initial={
                "submission_key": uuid.uuid4(),
                "material_version": material["version"],
            },
        )
        material["edit_form"] = MaterialForm(
            data=data if bound == f"edit-material-{material['id']}" else None,
            edit=True,
            categories=catalogue["categories"],
            units=catalogue["units"],
            initial={
                "submission_key": uuid.uuid4(),
                "name": material["name"],
                "category_id": material["category_id"],
                "category_version": material["category_version"],
                "unit_id": material["unit_id"],
                "unit_version": material["unit_version"],
            },
        )
        material["transition_form"] = MaterialTransitionForm(
            initial={"submission_key": uuid.uuid4(), "version": material["version"]}
        )
        for variant in material["variants"]:
            variant["auto_open_edit"] = bound == f"edit-variant-{variant['id']}"
            variant["edit_form"] = MaterialVariantForm(
                data=data if bound == f"edit-variant-{variant['id']}" else None,
                edit=True,
                initial={
                    "submission_key": uuid.uuid4(),
                    "spec_label": variant["label"],
                    "min_threshold": variant.get("min_threshold"),
                },
            )
            variant["transition_form"] = MaterialTransitionForm(
                initial={"submission_key": uuid.uuid4(), "version": variant["version"]}
            )


def _render_materials(
    request, access, *, result=None, bound=None, data=None, status=200
):
    fresh_access = resolve_materials_access(request.user)
    if fresh_access is None or fresh_access.mode != access.mode:
        return _denied(request)
    access = fresh_access
    catalogue = get_materials_catalogue(
        access.actor,
        search=request.GET.get("q", ""),
        status=request.GET.get("status"),
        category_id=request.GET.get("category"),
    )
    if catalogue is None:
        return _denied(request)
    _decorate_material_forms(catalogue, bound=bound, data=data)
    context = {
        **catalogue,
        "identity_user": access.actor,
        "result": result,
        "feedback": pop_feedback(request),
        "material_form": _material_forms(catalogue, bound=bound, data=data)
        if catalogue["mode"] == "admin"
        else None,
        "open_dialog": bound,
    }
    template = (
        "workshops/materials_admin.html"
        if access.mode == "admin"
        else "workshops/materials_readonly.html"
    )
    return render(request, template, context, status=status)


@require_GET
def materials(request):
    access = resolve_materials_access(request.user)
    if access is None:
        return _denied(request)
    return _render_materials(request, access)


def _material_get_url(request):
    query = urlencode(
        {
            "q": request.GET.get("q", ""),
            "status": request.GET.get("status", ""),
            "category": request.GET.get("category", ""),
        }
    )
    return f"{reverse('workshops:materials')}?{query}"


def _material_result_response(request, access, result, *, bound=None, data=None):
    if result.code in {"committed", "recovered"}:
        set_feedback(
            request,
            title="Change committed"
            if result.code == "committed"
            else "Change recovered",
            body="The material catalogue has been refreshed.",
        )
        return redirect(_material_get_url(request))
    return _render_materials(
        request,
        access,
        result=result.code,
        bound=bound,
        data=data,
        status=_material_result_status(result),
    )


@require_GET
def material_detail(request, material_id):
    access = resolve_materials_access(request.user)
    if access is None:
        return _denied(request)
    projection = get_material_detail(access.actor, material_id)
    if projection is None:
        raise Http404
    return render(
        request,
        "workshops/material_detail.html",
        {**projection, "identity_user": access.actor},
    )


@require_GET
def material_variant_detail(request, variant_id):
    access = resolve_materials_access(request.user)
    if access is None:
        return _denied(request)
    projection = get_material_variant_detail(access.actor, variant_id)
    if projection is None:
        raise Http404
    return render(
        request,
        "workshops/material_variant_detail.html",
        {**projection, "identity_user": access.actor},
    )


def _material_result_status(result):
    return 400 if result.code in {"invalid", "stale", "blocked", "unavailable"} else 200


@require_POST
def material_create(request):
    access = resolve_materials_access(request.user)
    if access is None or access.mode != "admin":
        return _denied(request)
    catalogue = get_materials_catalogue(access.actor)
    form = MaterialForm(
        request.POST,
        categories=catalogue["categories"],
        units=catalogue["units"],
    )
    if not form.is_valid():
        return _render_materials(
            request,
            access,
            result="invalid",
            bound="create-material",
            data=request.POST,
            status=400,
        )
    data = dict(form.cleaned_data)
    key = str(data.pop("submission_key"))
    result = create_material(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        submission_key=key,
        data=data,
    )
    return _material_result_response(request, access, result)


@require_POST
def material_edit(request, material_id):
    access = resolve_materials_access(request.user)
    if access is None or access.mode != "admin":
        return _denied(request)
    catalogue = get_materials_catalogue(access.actor)
    form = MaterialForm(
        request.POST,
        edit=True,
        categories=catalogue["categories"],
        units=catalogue["units"],
    )
    if not form.is_valid():
        return _render_materials(
            request,
            access,
            result="invalid",
            bound=f"edit-material-{material_id}",
            data=request.POST,
            status=400,
        )
    data = dict(form.cleaned_data)
    key = str(data.pop("submission_key"))
    result = edit_material(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        material_id=material_id,
        expected_version=request.POST.get("version"),
        idempotency_key=key,
        data=data,
    )
    return _material_result_response(
        request,
        access,
        result,
        bound=f"edit-material-{material_id}",
        data=request.POST,
    )


@require_POST
def material_transition(request, material_id, action):
    access = resolve_materials_access(request.user)
    if access is None or access.mode != "admin" or action not in {"retire", "restore"}:
        return _denied(request)
    form = MaterialTransitionForm(request.POST)
    if not form.is_valid():
        return _render_materials(request, access, result="invalid", status=400)
    result = transition_material(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        material_id=material_id,
        expected_version=form.cleaned_data["version"],
        idempotency_key=str(form.cleaned_data["submission_key"]),
        action="archive" if action == "retire" else "restore",
    )
    return _material_result_response(request, access, result)


@require_POST
def material_variant_create(request, material_id):
    access = resolve_materials_access(request.user)
    if access is None or access.mode != "admin":
        return _denied(request)
    form = MaterialVariantForm(request.POST)
    if not form.is_valid():
        return _render_materials(
            request,
            access,
            result="invalid",
            bound=f"create-variant-{material_id}",
            data=request.POST,
            status=400,
        )
    data = dict(form.cleaned_data)
    key = str(data.pop("submission_key"))
    result = create_material_variant(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        material_id=material_id,
        submission_key=key,
        data=data,
    )
    return _material_result_response(request, access, result)


@require_POST
def material_variant_edit(request, variant_id):
    access = resolve_materials_access(request.user)
    if access is None or access.mode != "admin":
        return _denied(request)
    form = MaterialVariantForm(request.POST, edit=True)
    if not form.is_valid():
        return _render_materials(
            request,
            access,
            result="invalid",
            bound=f"edit-variant-{variant_id}",
            data=request.POST,
            status=400,
        )
    data = dict(form.cleaned_data)
    key = str(data.pop("submission_key"))
    result = edit_material_variant(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        variant_id=variant_id,
        expected_version=request.POST.get("version"),
        idempotency_key=key,
        data=data,
    )
    return _material_result_response(
        request,
        access,
        result,
        bound=f"edit-variant-{variant_id}",
        data=request.POST,
    )


@require_POST
def material_variant_transition(request, variant_id, action):
    access = resolve_materials_access(request.user)
    if access is None or access.mode != "admin" or action not in {"retire", "restore"}:
        return _denied(request)
    form = MaterialTransitionForm(request.POST)
    if not form.is_valid():
        return _render_materials(request, access, result="invalid", status=400)
    result = transition_material_variant(
        actor_id=access.actor.id,
        workshop_id=access.workshop.id,
        variant_id=variant_id,
        expected_version=form.cleaned_data["version"],
        idempotency_key=str(form.cleaned_data["submission_key"]),
        action="archive" if action == "retire" else "restore",
    )
    return _material_result_response(request, access, result)
