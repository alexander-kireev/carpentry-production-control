"""Accounts views.

- ``register`` (A1; opened up in A2 per D-126): the public ``/register`` flow.
- The identity ChangeRequest HTTP surface (Slice D / D3): a non-admin's CR
  submission, the admin's own-profile direct identity edit, and the admin
  CR-only work queue (``/admin/requests``) with approve / reject. All the domain
  work lives in ``accounts.services``; these views only translate HTTP in/out
  (form binding, permission gating, messages, redirects).
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.forms import (
    AdminIdentityForm,
    AdminRegisterForm,
    ChangeRequestForm,
    RejectReasonForm,
)
from accounts.models import ChangeRequest, User
from accounts.services import (
    IDENTITY_FIELDS,
    ChangeRequestError,
    apply_identity_change,
    approve_cr,
    describe_change_request,
    register_admin,
    reject_cr,
    submit_cr,
)


def register(request):
    if request.method == "POST":
        form = AdminRegisterForm(request.POST)
        if form.is_valid():
            user = register_admin(form)
            # The user was created via set_password (not authenticate()), so it
            # has no .backend attribute; login() requires one.
            user.backend = "django.contrib.auth.backends.ModelBackend"
            login(request, user)
            return redirect("/workshop/setup")
    else:
        form = AdminRegisterForm()

    return render(request, "registration/register.html", {"form": form})


# --------------------------------------------------------------------------- #
# Identity ChangeRequest workflow (Slice D / D3)
# --------------------------------------------------------------------------- #


def _require_admin(request):
    """Gate an action to the workshop admin (raises 403 otherwise)."""
    if request.user.account_role != User.AccountRole.ADMIN:
        raise PermissionDenied("Only an admin can review change requests.")


def _form_errors_message(form) -> str:
    """Flatten a bound form's errors into a single user-facing sentence."""
    bits = [str(error) for errors in form.errors.values() for error in errors]
    return " ".join(bits)


@require_POST
@login_required
def submit_change_request(request):
    """A non-admin submits a CR for one of their own identity fields → ``submit_cr``.

    Redirect-back with a message (the form lives in a modal on the profile page).
    Admins never reach this — they edit their identity directly — so an admin
    actor is denied rather than 500'd by the service guard.
    """
    if request.user.account_role == User.AccountRole.ADMIN:
        raise PermissionDenied("Admins edit their identity directly.")

    target_field = request.POST.get("target_field", "")
    if target_field not in IDENTITY_FIELDS:
        return HttpResponseBadRequest("Unknown field.")

    # The profile modal renders each field's form with prefix=target_field to keep
    # ids unique across the per-field modals; bind with the same prefix.
    form = ChangeRequestForm(request.POST, target_field=target_field, prefix=target_field)
    if not form.is_valid():
        messages.warning(
            request,
            _form_errors_message(form) or "Please correct the change request.",
        )
        return redirect(reverse("profile"))

    try:
        submit_cr(
            request.user,
            target_field,
            form.cleaned_data["proposed_value"],
            form.cleaned_data["reason"],
        )
    except ChangeRequestError as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(
            request,
            "Your change request has been submitted — this will take effect "
            "once approved.",
        )
    return redirect(reverse("profile"))


@require_POST
@login_required
def admin_identity_edit(request):
    """The admin edits their **own** identity directly (mandatory reason, no CR).

    Each changed field is routed through ``apply_identity_change`` (the same
    service Slice B's Edit User panel will reuse), and the whole multi-field edit
    is bracketed in one transaction so a mid-loop failure can't half-apply it.
    """
    _require_admin(request)

    # Baseline the stored values before binding — ``ModelForm`` mutates
    # ``request.user`` in ``_post_clean``, so read the originals first.
    baseline = {field: getattr(request.user, field) for field in IDENTITY_FIELDS}
    form = AdminIdentityForm(request.POST, instance=request.user)
    if not form.is_valid():
        messages.warning(
            request, _form_errors_message(form) or "Please correct the form."
        )
        return redirect(reverse("profile"))

    reason = form.cleaned_data["reason"]
    changed = [f for f in IDENTITY_FIELDS if form.cleaned_data[f] != baseline[f]]
    if not changed:
        messages.info(request, "No changes to save.")
        return redirect(reverse("profile"))

    with transaction.atomic():
        for field in changed:
            apply_identity_change(
                request.user, field, form.cleaned_data[field], reason, request.user
            )
    messages.success(request, "Your details have been updated.")
    return redirect(reverse("profile"))


@login_required
def admin_requests(request):
    """The admin's CR-only work queue: pending identity CRs in their workshop.

    Workshop-scoped (D-126) — an admin only ever sees their own workshop's CRs.
    """
    _require_admin(request)
    pending = (
        ChangeRequest.objects.filter(
            workshop=request.user.workshop,
            status=ChangeRequest.Status.PENDING,
        )
        .select_related("requested_by")
        .order_by("created_at")
    )
    rows = [describe_change_request(cr) for cr in pending]
    return render(request, "accounts/admin_requests.html", {"rows": rows})


@require_POST
@login_required
def approve_change_request(request, pk):
    """Approve a pending CR (auto-applies the change). Admin-only, workshop-scoped."""
    _require_admin(request)
    cr = get_object_or_404(ChangeRequest, pk=pk, workshop=request.user.workshop)
    try:
        approve_cr(cr, request.user, note=request.POST.get("note", "").strip() or None)
    except ChangeRequestError as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(request, f"{cr.code} approved — the change has been applied.")
    return redirect(reverse("admin_requests"))


@require_POST
@login_required
def reject_change_request(request, pk):
    """Reject a pending CR with a mandatory reason. Admin-only, workshop-scoped."""
    _require_admin(request)
    cr = get_object_or_404(ChangeRequest, pk=pk, workshop=request.user.workshop)
    form = RejectReasonForm(request.POST)
    if not form.is_valid():
        messages.warning(
            request, "A reason is required to reject a change request."
        )
        return redirect(reverse("admin_requests"))
    try:
        reject_cr(cr, request.user, form.cleaned_data["note"])
    except ChangeRequestError as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(request, f"{cr.code} rejected.")
    return redirect(reverse("admin_requests"))
