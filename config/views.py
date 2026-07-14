"""Project-level views.

``health`` is the container/health-check endpoint the F1 smoke test relies on.
``home`` is F2's authenticated landing placeholder — S1 turns this into role
dispatch.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def health(request: HttpRequest) -> JsonResponse:
    """Return HTTP 200 to confirm the application is running."""
    return JsonResponse({"status": "ok"})


@login_required
def home(request: HttpRequest) -> HttpResponse:
    """Authenticated landing placeholder (role dispatch arrives in S1)."""
    return render(request, "home.html")
