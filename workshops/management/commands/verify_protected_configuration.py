from django.core.management.base import BaseCommand, CommandError

from workshops.models import Workshop
from workshops.protected_configuration import (
    ProtectedConfigurationError,
    resolve_protected_configuration,
    verify_workshop_protected_pair,
)


class Command(BaseCommand):
    help = "Verify required protected configuration without changing it."

    def handle(self, *args, **options):
        try:
            resolve_protected_configuration()
            for workshop in Workshop.objects.order_by("id"):
                verify_workshop_protected_pair(workshop)
        except ProtectedConfigurationError as error:
            raise CommandError("Protected configuration verification failed") from error
        self.stdout.write("Protected configuration verification succeeded")
