"""Workshop setup-gate middleware (A2) — replaces the S1 pass-through stub.

Per-user gate (D-126): an authenticated user whose own Workshop does not yet
exist (``request.user.workshop_id is None``) is redirected to
``/workshop/setup`` on every route except the setup page itself, logout, and
static assets — those three are excluded to avoid a redirect loop. It is never
a global ``Workshop.objects.count()`` check: another admin's workshop has no
bearing on this user's gate.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class WorkshopSetupGateMiddleware:
    """Redirect authenticated, workshop-less users to Workshop setup."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and user.workshop_id is None
            and not self._is_exempt(request.path)
        ):
            return redirect(reverse("workshop_setup"))
        return self.get_response(request)

    @staticmethod
    def _is_exempt(path):
        """Setup target, logout, and static assets — the loop-avoiding exemptions."""
        static_prefix = settings.STATIC_URL or ""
        if not static_prefix.startswith("/"):
            static_prefix = "/" + static_prefix
        return (
            path == reverse("workshop_setup")
            or path == reverse("logout")
            or path.startswith(static_prefix)
        )
