import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from .models import EventNotificationIntent, Notification
from .recipient_policies import resolve_recipients

logger = logging.getLogger("events")
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ProcessingCounts:
    claimed: int = 0
    processed: int = 0
    failed: int = 0
    notifications: int = 0


class IntentProcessingError(Exception):
    def __init__(self, intent_id, cause):
        self.intent_id = intent_id
        self.cause = cause
        super().__init__("Event intent processing failed")


def _claim_and_process():
    with transaction.atomic():
        intent = (
            EventNotificationIntent.objects.select_for_update(skip_locked=True)
            .filter(status=EventNotificationIntent.Status.PENDING)
            .order_by("created_at", "id")
            .first()
        )
        if intent is None:
            return None
        try:
            intent.status = EventNotificationIntent.Status.PROCESSING
            intent.attempts += 1
            intent.last_attempted_at = timezone.now()
            intent.save(update_fields=("status", "attempts", "last_attempted_at"))
            recipients = resolve_recipients(intent.event)
            inserted = 0
            for recipient in recipients:
                _, created = Notification.objects.get_or_create(
                    event=intent.event,
                    recipient_user_id=recipient.user_id,
                    defaults={
                        "title_snapshot": recipient.title,
                        "body_snapshot": recipient.body,
                    },
                )
                inserted += int(created)
            intent.status = EventNotificationIntent.Status.PROCESSED
            intent.save(update_fields=("status",))
            return intent.id, inserted
        except Exception as exception:
            transaction.set_rollback(True)
            raise IntentProcessingError(intent.id, exception) from exception


def _record_failure(intent_id, exception):
    with transaction.atomic():
        intent = EventNotificationIntent.objects.select_for_update().get(pk=intent_id)
        if intent.status != EventNotificationIntent.Status.PENDING:
            return
        intent.attempts += 1
        intent.last_attempted_at = timezone.now()
        intent.status = (
            EventNotificationIntent.Status.FAILED
            if intent.attempts >= MAX_ATTEMPTS
            else EventNotificationIntent.Status.PENDING
        )
        intent.save(update_fields=("status", "attempts", "last_attempted_at"))
        logger.error(
            "Event intent processing failed",
            extra={
                "operation": "events.notification.process",
                "result_code": "failed",
                "intent_id": intent.id,
                "attempt": intent.attempts,
                "status": intent.status,
                "exception_class": exception.__class__.__name__,
            },
        )


def process_event_notification_intents(*, limit=100):
    claimed = processed = failed = notifications = 0
    for _ in range(max(0, limit)):
        try:
            outcome = _claim_and_process()
            if outcome is None:
                break
            claimed += 1
            _, inserted = outcome
            notifications += inserted
            processed += 1
        except IntentProcessingError as exception:
            claimed += 1
            _record_failure(exception.intent_id, exception.cause)
            failed += 1
    return ProcessingCounts(claimed, processed, failed, notifications)
