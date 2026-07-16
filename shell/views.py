"""Shell views: role dispatch, role landings, shared Profile, Analytics placeholder,
and the DEBUG-only "view as role" switcher. Static shell only — no backend."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from accounts.forms import ProfilePhoneForm
from accounts.models import User
from accounts.services import set_own_phone
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
        context["is_admin"] = user.account_role == User.AccountRole.ADMIN
        context.setdefault("phone_form", ProfilePhoneForm(instance=user))
        return context

    def post(self, request, *args, **kwargs):
        """Persist the owner's inline phone edit — the one live write here.

        Post/redirect/get so a refresh doesn't resubmit; an invalid value
        re-renders the page with the bound form's error.
        """
        form = ProfilePhoneForm(request.POST, instance=request.user)
        if form.is_valid():
            set_own_phone(request.user, form.cleaned_data["phone"])
            messages.success(request, "Phone number updated.")
            return redirect(reverse("profile"))
        return self.render_to_response(self.get_context_data(phone_form=form))


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
