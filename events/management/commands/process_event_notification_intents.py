from django.core.management.base import BaseCommand, CommandError

from events.processing import process_event_notification_intents


class Command(BaseCommand):
    help = "Process a finite batch of pending Event notification intents."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 1000:
            raise CommandError("--limit must be between 1 and 1000")
        result = process_event_notification_intents(limit=limit)
        self.stdout.write(
            " ".join(
                (
                    f"claimed={result.claimed}",
                    f"processed={result.processed}",
                    f"failed={result.failed}",
                    f"notifications={result.notifications}",
                )
            )
        )
