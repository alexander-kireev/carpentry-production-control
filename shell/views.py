"""Shell views: role dispatch, role landings, shared Profile, Analytics placeholder,
and the DEBUG-only "view as role" switcher. Static shell only — no backend."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.defaultfilters import date as date_filter
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from accounts.forms import AdminIdentityForm, ChangeRequestForm, ProfilePhoneForm
from accounts.models import ChangeRequest, User
from accounts.services import (
    IDENTITY_FIELD_LABELS,
    IDENTITY_FIELDS,
    describe_change_request,
    set_own_phone,
)
from catalog.library_config import DISPLAY_LIBRARY_TYPES
from shell.roles import OVERRIDE_SESSION_KEY, ROLE_LANDING, get_effective_role


@login_required
def root(request):
    """Dispatch an authenticated user to their effective-role landing."""
    route_name = ROLE_LANDING.get(get_effective_role(request))
    if route_name is None:
        return HttpResponseBadRequest("No landing page for this account role.")
    return redirect(reverse(route_name))


class AdminDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "shell/admin/dashboard.html"


class ManagerDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "shell/manager/dashboard.html"


class OperatorDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "shell/operator/dashboard.html"


class TechnicianDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "shell/technician/dashboard.html"


class ProfileView(LoginRequiredMixin, TemplateView):
    """Shared, role-adaptive Profile page (Slice D / D2).

    Renders the logged-in user's own identity, personal information (with the
    self-service phone edit), and role & skills, adapting to whether the user is
    the admin. Login + the workshop-setup gate run first (KI-021), so the page
    only ever shows ``request.user``'s own data — no cross-workshop query. Role
    adaptation keys off the real ``account_role`` (the profile is the user's own
    record; the DEBUG "view as role" switcher is a nav preview and must not fake
    the identity shown here).
    """

    template_name = "shell/profile/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        is_admin = user.account_role == User.AccountRole.ADMIN
        context["is_admin"] = is_admin
        context.setdefault("phone_form", ProfilePhoneForm(instance=user))
        if is_admin:
            # Admin edits their own identity directly (D3): editable inputs in a
            # mandatory-reason modal → accounts.admin_identity_edit.
            context.setdefault("admin_identity_form", AdminIdentityForm(instance=user))
        else:
            # Non-admin: identity stays read-only, each field gaining a
            # "Request change" modal → accounts.submit_change_request. A pending
            # CR blocks a new submission (the one-pending guard), so surface it
            # and disable the buttons rather than let the submit bounce back.
            context["pending_cr"] = ChangeRequest.objects.filter(
                requested_by=user, status=ChangeRequest.Status.PENDING
            ).first()
            context["identity_fields"] = [
                {
                    "name": name,
                    "label": IDENTITY_FIELD_LABELS[name],
                    "display": self._identity_display(user, name),
                    "form": ChangeRequestForm(target_field=name, prefix=name),
                }
                for name in IDENTITY_FIELDS
            ]
        return context

    @staticmethod
    def _identity_display(user, name):
        value = getattr(user, name)
        if name == "date_of_birth":
            return date_filter(value, "j M Y")
        return value

    def post(self, request, *args, **kwargs):
        """Persist the owner's inline phone edit — the one live write here.

        Post/redirect/get so a refresh doesn't resubmit; an invalid value
        re-renders the page with the bound form's error. The identity-change
        writes post to their own accounts routes, not here.
        """
        form = ProfilePhoneForm(request.POST, instance=request.user)
        if form.is_valid():
            set_own_phone(request.user, form.cleaned_data["phone"])
            messages.success(request, "Phone number updated.")
            return redirect(reverse("profile"))
        return self.render_to_response(self.get_context_data(phone_form=form))


class RequesterTrackingView(LoginRequiredMixin, TemplateView):
    """A requester's read-only "Your requests" surface (Slice D / D3).

    Lists the logged-in user's own identity ChangeRequests (all statuses, newest
    first), with the distinct "Superseded" state and any rejection note. There is
    **no Cancel action** — a CR can't be withdrawn (system-cancellation only, per
    the CR state machine). ``template_name`` is set per route: the operator /
    technician Requests page and the manager Work Feed each host this section.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        crs = ChangeRequest.objects.filter(
            requested_by=self.request.user
        ).order_by("-created_at")
        context["your_requests"] = [describe_change_request(cr) for cr in crs]
        return context


class AnalyticsPlaceholderView(LoginRequiredMixin, TemplateView):
    """The one Analytics static placeholder (visibly flagged; not built in Phase 1)."""

    template_name = "shell/analytics_placeholder.html"


class ShellPageView(LoginRequiredMixin, TemplateView):
    """A hollow S2 page skeleton: ``_page_header`` + empty structural section(s).

    Static shell only — no data or logic. Each route sets ``template_name`` via
    ``as_view`` (see ``shell/urls.py``); the real interior is delivered by the
    owning feature slice. No per-role guard here — page-level permissions are
    Slice H; login is the only gate, as with the S1 dashboards.
    """


class AdminWorkshopView(ShellPageView):
    """Admin Workshop skeleton — the first consumer of ``_tabs.html``.

    Tab tuples are ``(id, label, pane_template)``; a ``None`` pane stays empty.
    Slice C fills the Libraries pane (the six library-card types, WorkshopRole
    added in C2), the Stations pane, and the Materials pane; the Users & Roles
    pane is filled by Slice B later.
    """

    template_name = "shell/admin/workshop.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tabs"] = [
            ("users-roles", "Users & Roles", None),
            ("stations", "Stations", "catalog/admin/_stations_pane.html"),
            ("materials", "Materials", "catalog/admin/_materials_pane.html"),
            ("libraries", "Libraries", "catalog/admin/_libraries_pane.html"),
        ]
        context["libraries"] = DISPLAY_LIBRARY_TYPES
        return context


@require_POST
@login_required
def debug_view_as(request):
    """DEBUG-only: set/clear the session role override, then re-dispatch via ``root``.

    Inert in production: returns 404 when ``settings.DEBUG`` is false, so no
    override can ever be written outside a debug environment.
    """
    if not settings.DEBUG:
        raise Http404
    role = request.POST.get("role", "")
    if role in User.AccountRole.values:
        request.session[OVERRIDE_SESSION_KEY] = role
    else:
        request.session.pop(OVERRIDE_SESSION_KEY, None)
    return redirect(reverse("root"))
