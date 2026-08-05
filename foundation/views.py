"""Database-independent views for the Django foundation."""

import logging

from django.http import JsonResponse
from django.shortcuts import render

logger = logging.getLogger("foundation")


def root(request):
    logger.info(
        "Foundation page served",
        extra={"operation": "foundation.root", "result_code": "ok"},
    )
    return render(request, "base.html")


def health(request):
    logger.info(
        "Liveness check served",
        extra={"operation": "foundation.health", "result_code": "ok"},
    )
    return JsonResponse({"status": "ok"})
