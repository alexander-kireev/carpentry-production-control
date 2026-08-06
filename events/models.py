from django.conf import settings
from django.db import models
from django.db.models.expressions import RawSQL


class Event(models.Model):
    class ActorType(models.TextChoices):
        USER = "user", "User"
        SYSTEM = "system", "System"
        SCHEDULED_PROCESS = "scheduled_process", "Scheduled process"
        EXTERNAL_SYSTEM = "external_system", "External system"

    sequence_number = models.BigIntegerField(
        unique=True,
        db_default=RawSQL("nextval('event_sequence_number_seq'::regclass)", ()),
    )
    event_type = models.TextField()
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(db_default=RawSQL("now()", ()))
    actor_type = models.TextField(choices=ActorType.choices)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="domain_events",
    )
    primary_subject_type = models.TextField(null=True, blank=True)
    primary_subject_id = models.BigIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, db_default={})
    causation_event = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="caused_events",
    )
    idempotency_key = models.TextField(null=True, blank=True)
    correlation_key = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "event"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    actor_type__in=(
                        "user",
                        "system",
                        "scheduled_process",
                        "external_system",
                    )
                ),
                name="cst_033_event_actor_type",
            ),
            models.CheckConstraint(
                condition=~models.Q(actor_type="user")
                | models.Q(actor_user__isnull=False),
                name="cst_034_event_user_actor",
            ),
            models.UniqueConstraint(
                fields=("event_type", "idempotency_key"),
                condition=models.Q(idempotency_key__isnull=False),
                name="cst_036_event_producer_key_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=("event_type",), name="idx_009_event_type"),
            models.Index(
                fields=("primary_subject_type", "primary_subject_id"),
                name="idx_010_event_subject",
            ),
            models.Index(
                fields=("correlation_key",),
                condition=models.Q(correlation_key__isnull=False),
                name="idx_011_event_correlation",
            ),
            models.Index(fields=("causation_event",), name="idx_012_event_causation"),
        ]


class EventNotificationIntent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    event = models.OneToOneField(
        Event, on_delete=models.RESTRICT, related_name="notification_intent"
    )
    status = models.TextField(
        choices=Status.choices, default=Status.PENDING, db_default="pending"
    )
    attempts = models.PositiveIntegerField(default=0, db_default=0)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "event_notification_intent"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("pending", "processing", "processed", "failed")
                ),
                name="cst_682_event_intent_status",
            ),
            models.CheckConstraint(
                condition=models.Q(attempts__gte=0) & models.Q(attempts__lte=3),
                name="cst_683_event_intent_attempts",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(attempts=0, last_attempted_at__isnull=True)
                    | models.Q(attempts__gt=0, last_attempted_at__isnull=False)
                ),
                name="cst_684_event_intent_attempt_shape",
            ),
        ]
        indexes = [
            models.Index(
                fields=("created_at",),
                condition=models.Q(status="pending"),
                name="idx_013_event_intent_pending",
            )
        ]


class Notification(models.Model):
    class Status(models.TextChoices):
        UNREAD = "unread", "Unread"
        READ = "read", "Read"
        DISMISSED = "dismissed", "Dismissed"

    event = models.ForeignKey(
        Event, on_delete=models.RESTRICT, related_name="notifications"
    )
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name="notifications",
    )
    title_snapshot = models.TextField()
    body_snapshot = models.TextField()
    status = models.TextField(
        choices=Status.choices, default=Status.UNREAD, db_default="unread"
    )
    pinned = models.BooleanField(default=False, db_default=False)
    important = models.BooleanField(default=False, db_default=False)
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "notification"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("unread", "read", "dismissed")),
                name="cst_685_notification_status",
            ),
            models.UniqueConstraint(
                fields=("event", "recipient_user"),
                name="cst_037_notification_event_recipient_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("recipient_user", "status"),
                name="idx_014_notification_inbox",
            )
        ]
