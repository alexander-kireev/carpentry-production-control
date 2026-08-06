"""Database-independent views for the Django foundation."""

import logging

from django.http import JsonResponse
from django.shortcuts import redirect

from identity.queries import resolve_authenticated_destination

logger = logging.getLogger("foundation")


def root(request):
    logger.info(
        "Foundation page served",
        extra={"operation": "foundation.root", "result_code": "ok"},
    )
    result = resolve_authenticated_destination(request.user)
    return redirect(result.destination.value)


def health(request):
    logger.info(
        "Liveness check served",
        extra={"operation": "foundation.health", "result_code": "ok"},
    )
    return JsonResponse({"status": "ok"})
