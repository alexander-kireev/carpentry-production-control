"""Project-level views.

``health`` is the container/health-check endpoint the F1 smoke test relies on.
The authenticated landing (``/``) is now role dispatch — see ``shell.views.root``.
"""

from django.http import HttpRequest, JsonResponse


def health(request: HttpRequest) -> JsonResponse:
    """Return HTTP 200 to confirm the application is running."""
    return JsonResponse({"status": "ok"})
