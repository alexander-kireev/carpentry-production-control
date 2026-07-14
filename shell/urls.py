"""Shell app URLs: role landings, shared pages, and the DEBUG switcher.

Flat routes, no trailing slash (D-123/CHG-054; ``/health/`` is F1's exempt
infra route). S2-S5 register their per-role child pages as flat paths too, e.g.
``path("admin/workshop", ...)``.
"""

from django.urls import path

from shell.views import (
    AdminDashboardView,
    AnalyticsPlaceholderView,
    ManagerDashboardView,
    OperatorDashboardView,
    ProfileView,
    TechnicianDashboardView,
    debug_view_as,
)

urlpatterns = [
    # Role landings
    path("admin", AdminDashboardView.as_view(), name="admin_dashboard"),
    path("manager", ManagerDashboardView.as_view(), name="manager_dashboard"),
    path("operator", OperatorDashboardView.as_view(), name="operator_dashboard"),
    path("tech", TechnicianDashboardView.as_view(), name="technician_dashboard"),
    # Shared pages
    path("profile", ProfileView.as_view(), name="profile"),
    path("analytics", AnalyticsPlaceholderView.as_view(), name="analytics"),
    # DEBUG-only "view as role" switcher
    path("debug/view-as", debug_view_as, name="debug_view_as"),
]
