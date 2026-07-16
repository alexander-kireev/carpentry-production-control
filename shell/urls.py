"""Shell app URLs: role landings, shared pages, and the DEBUG switcher.

Flat routes, no trailing slash (D-123/CHG-054; ``/health/`` is F1's exempt
infra route). S2-S5 register their per-role child pages as flat paths too, e.g.
``path("admin/workshop", ...)``.
"""

from django.urls import path

from shell.views import (
    AdminDashboardView,
    AdminWorkshopView,
    AnalyticsPlaceholderView,
    ManagerDashboardView,
    OperatorDashboardView,
    ProfileView,
    RequesterTrackingView,
    ShellPageView,
    TechnicianDashboardView,
    debug_view_as,
)

urlpatterns = [
    # Role landings
    path("admin", AdminDashboardView.as_view(), name="admin_dashboard"),
    path("manager", ManagerDashboardView.as_view(), name="manager_dashboard"),
    path("operator", OperatorDashboardView.as_view(), name="operator_dashboard"),
    path("tech", TechnicianDashboardView.as_view(), name="technician_dashboard"),
    # Shared pages (one route, linked from every role's nav)
    path("profile", ProfileView.as_view(), name="profile"),
    path("analytics", AnalyticsPlaceholderView.as_view(), name="analytics"),
    path(
        "messages",
        ShellPageView.as_view(template_name="shell/messages.html"),
        name="messages",
    ),
    path(
        "notifications",
        ShellPageView.as_view(template_name="shell/notifications.html"),
        name="notifications",
    ),
    # --- S2 role page skeletons (hollow; interiors are each slice's) ----------
    # Admin
    path(
        "admin/my-work",
        ShellPageView.as_view(template_name="shell/admin/my_work.html"),
        name="admin_my_work",
    ),
    path("admin/workshop", AdminWorkshopView.as_view(), name="admin_workshop"),
    path(
        "admin/business",
        ShellPageView.as_view(template_name="shell/admin/business.html"),
        name="admin_business",
    ),
    # Manager
    path(
        "manager/my-work",
        RequesterTrackingView.as_view(template_name="shell/manager/my_work.html"),
        name="manager_my_work",
    ),
    path(
        "manager/orders",
        ShellPageView.as_view(template_name="shell/manager/orders.html"),
        name="manager_orders",
    ),
    path(
        "manager/schedule",
        ShellPageView.as_view(template_name="shell/manager/schedule.html"),
        name="manager_schedule",
    ),
    path(
        "manager/workshop",
        ShellPageView.as_view(template_name="shell/manager/workshop.html"),
        name="manager_workshop",
    ),
    # Operator
    path(
        "operator/my-work",
        ShellPageView.as_view(template_name="shell/operator/my_work.html"),
        name="operator_my_work",
    ),
    path(
        "operator/schedules",
        ShellPageView.as_view(template_name="shell/operator/schedules.html"),
        name="operator_schedules",
    ),
    path(
        "operator/workshop",
        ShellPageView.as_view(template_name="shell/operator/workshop.html"),
        name="operator_workshop",
    ),
    path(
        "operator/requests",
        RequesterTrackingView.as_view(template_name="shell/operator/requests.html"),
        name="operator_requests",
    ),
    # Technician
    path(
        "tech/my-work",
        ShellPageView.as_view(template_name="shell/technician/my_work.html"),
        name="technician_my_work",
    ),
    path(
        "tech/schedules",
        ShellPageView.as_view(template_name="shell/technician/schedules.html"),
        name="technician_schedules",
    ),
    path(
        "tech/workshop",
        ShellPageView.as_view(template_name="shell/technician/workshop.html"),
        name="technician_workshop",
    ),
    path(
        "tech/orders",
        ShellPageView.as_view(template_name="shell/technician/orders.html"),
        name="technician_orders",
    ),
    path(
        "tech/requests",
        RequesterTrackingView.as_view(template_name="shell/technician/requests.html"),
        name="technician_requests",
    ),
    # DEBUG-only "view as role" switcher
    path("debug/view-as", debug_view_as, name="debug_view_as"),
]
