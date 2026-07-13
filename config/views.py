"""Project-level views.

A minimal health/root endpoint so the F1 smoke test and the container health
check can confirm the app is up. F2 introduces the real templated home page.
"""

from django.http import HttpRequest, JsonResponse


def health(request: HttpRequest) -> JsonResponse:
    """Return HTTP 200 to confirm the application is running."""
    return JsonResponse({"status": "ok"})
