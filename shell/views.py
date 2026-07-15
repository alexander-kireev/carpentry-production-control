"""Shell views: role dispatch, role landings, shared Profile, Analytics placeholder,
and the DEBUG-only "view as role" switcher. Static shell only — no backend."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from accounts.models import User
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
    """Shared, role-adaptive Profile shell (static; behaviour is Slice D)."""

    template_name = "shell/profile/profile.html"


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
    Slice C fills the Libraries pane (the five dependency-free types); the other
    three panes are filled by Slices B and C2 later.
    """

    template_name = "shell/admin/workshop.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tabs"] = [
            ("users-roles", "Users & Roles", None),
            ("stations", "Stations", None),
            ("materials", "Materials", None),
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
