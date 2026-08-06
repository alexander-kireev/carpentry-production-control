from django.core.management.base import BaseCommand, CommandError

from workshops.protected_configuration import (
    ProtectedConfigurationError,
    resolve_protected_configuration,
)


class Command(BaseCommand):
    help = "Verify required protected configuration without changing it."

    def handle(self, *args, **options):
        try:
            resolve_protected_configuration()
        except ProtectedConfigurationError as error:
            raise CommandError("Protected configuration verification failed") from error
        self.stdout.write("Protected configuration verification succeeded")
