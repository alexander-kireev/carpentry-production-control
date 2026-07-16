"""Notifications URLs (N2): the list page + per-item actions + Mark-all-read.

Flat routes, no trailing slash (D-123). Deliberately **not** namespaced (no
``app_name``): the nav partials link the page by the bare name ``notifications``,
so keeping the names unnamespaced avoids reworking those references. The list
route takes over the placeholder previously served from ``shell/urls.py``.
"""

from django.urls import path

from notifications import views

urlpatterns = [
    path("notifications", views.notifications_page, name="notifications"),
    path(
        "notifications/mark-all-read",
        views.mark_all_read,
        name="notifications_mark_all_read",
    ),
    path("notifications/<int:pk>/select", views.select_notification, name="notification_select"),
    path("notifications/<int:pk>/open", views.open_notification, name="notification_open"),
    path("notifications/<int:pk>/read", views.mark_read, name="notification_read"),
    path("notifications/<int:pk>/unread", views.mark_unread, name="notification_unread"),
    path("notifications/<int:pk>/dismiss", views.dismiss, name="notification_dismiss"),
    path("notifications/<int:pk>/pin", views.toggle_pinned, name="notification_pin"),
    path(
        "notifications/<int:pk>/important",
        views.toggle_important,
        name="notification_important",
    ),
]
