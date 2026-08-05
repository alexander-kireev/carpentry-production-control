"""Django application configuration for foundation diagnostics."""

import logging

from django.apps import AppConfig

logger = logging.getLogger("foundation")


class FoundationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "foundation"

    def ready(self):
        logger.info(
            "Django foundation is ready",
            extra={"operation": "foundation.django_ready", "result_code": "ready"},
        )
