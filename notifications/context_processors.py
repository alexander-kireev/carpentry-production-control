"""Template context for the nav unread badge (N2).

Supplies ``notifications_unread_count`` to every template so the four role nav
partials can render the Notifications badge. Runs on every request — including
the login page and any anonymous request — so it must guard ``AnonymousUser``
(which has no ``notifications``) exactly like ``shell.context_processors`` does,
returning zero rather than querying.
"""

from notifications.models import Notification


def unread_count(request):
    if not request.user.is_authenticated:
        return {"notifications_unread_count": 0}
    return {
        "notifications_unread_count": Notification.objects.filter(
            recipient=request.user, status=Notification.Status.UNREAD
        ).count()
    }
