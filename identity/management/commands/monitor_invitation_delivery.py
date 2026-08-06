from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from identity.delivery import invitation_delivery_alerts


class Command(BaseCommand):
    help = "Report aged invitation delivery evidence without sending or mutating."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-minutes", type=int, default=15)

    def handle(self, *args, **options):
        minutes = options["older_than_minutes"]
        if minutes < 0:
            raise CommandError("older-than-minutes must be zero or greater")
        alerts = invitation_delivery_alerts(older_than=timedelta(minutes=minutes))
        for alert in alerts:
            self.stdout.write(
                " ".join(
                    (
                        f"intent_id={alert['intent_id']}",
                        f"invitation_id={alert['invitation_id']}",
                        f"generation={alert['generation']}",
                        f"status={alert['status']}",
                        f"age_seconds={alert['age_seconds']}",
                    )
                )
            )
        self.stdout.write(f"alert_count={len(alerts)}")
