"""Accounts URLs: the identity ChangeRequest HTTP surface (Slice D / D3).

Flat routes, no trailing slash (D-123). Left unnamespaced (no ``app_name``),
matching the config-level ``register`` route and the shell routes these views'
templates sit alongside — so ``{% url %}`` refs stay uniform rather than
half-namespaced. Route names are globally distinct.
"""

from django.urls import path

from accounts import views

urlpatterns = [
    path(
        "change-requests/submit",
        views.submit_change_request,
        name="submit_change_request",
    ),
    path(
        "change-requests/identity",
        views.admin_identity_edit,
        name="admin_identity_edit",
    ),
    path("admin/requests", views.admin_requests, name="admin_requests"),
    path(
        "admin/requests/<int:pk>/approve",
        views.approve_change_request,
        name="approve_change_request",
    ),
    path(
        "admin/requests/<int:pk>/reject",
        views.reject_change_request,
        name="reject_change_request",
    ),
]
