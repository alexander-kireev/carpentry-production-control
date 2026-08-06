import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workshops.models import Workshop

from .models import EmailDeliveryIntent, User, UserInvitation
from .results import ResultCode

logger = logging.getLogger("identity")


@dataclass(frozen=True)
class DeliveryResult:
    code: ResultCode
    intent_id: int | None = None


def _deliver(*, recipient, subject, body, from_email):
    """Deliver once without logging or persisting message or credentials."""
    mode = settings.INVITATION_DELIVERY_MODE
    if mode == "failing":
        raise RuntimeError("Invitation delivery adapter failed")
    if mode == "memory":
        return None
    message = EmailMessage()
    message["From"] = from_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP(
        settings.INVITATION_SMTP_HOST,
        int(settings.INVITATION_SMTP_PORT),
        timeout=int(settings.INVITATION_SMTP_TIMEOUT_SECONDS),
    ) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        client.login(
            settings.INVITATION_SMTP_USERNAME, settings.INVITATION_SMTP_API_KEY
        )
        refused = client.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def _validate_recipient_for_delivery(recipient):
    if settings.INVITATION_DELIVERY_MODE != "live":
        return
    if (
        settings.INVITATION_ENVIRONMENT != "production"
        and recipient.casefold() not in settings.INVITATION_RECIPIENT_ALLOWLIST
    ):
        raise ValueError("Invitation recipient is not allowlisted")


def _claim_intent(*, intent_id, invitation_id, generation):
    identifiers = (
        EmailDeliveryIntent.objects.filter(pk=intent_id)
        .values("invitation__workshop_id", "invitation__user_id")
        .first()
    )
    if identifiers is None:
        return None
    with transaction.atomic():
        workshop = Workshop.objects.select_for_update().get(
            pk=identifiers["invitation__workshop_id"]
        )
        candidate = User.objects.select_for_update().get(
            pk=identifiers["invitation__user_id"]
        )
        invitation = UserInvitation.objects.select_for_update().get(pk=invitation_id)
        intent = EmailDeliveryIntent.objects.select_for_update().get(pk=intent_id)
        exact = (
            workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING
            and candidate.workshop_id == workshop.id
            and candidate.account_role == User.AccountRole.MANAGER
            and candidate.status == User.Status.PENDING
            and invitation.user_id == candidate.id
            and invitation.workshop_id == workshop.id
            and invitation.status == UserInvitation.Status.PENDING
            and invitation.invitation_generation == generation
            and intent.invitation_id == invitation.id
            and intent.invitation_generation == generation
            and intent.status == EmailDeliveryIntent.Status.PENDING
            and intent.attempt_count == 0
        )
        if not exact:
            return None
        intent.attempt_count = 1
        intent.last_attempted_at = timezone.now()
        intent.save(update_fields=("attempt_count", "last_attempted_at"))
        return intent.recipient_email


def _record_outcome(*, intent_id, invitation_id, generation, status):
    identifiers = (
        EmailDeliveryIntent.objects.filter(pk=intent_id)
        .values("invitation__workshop_id", "invitation__user_id")
        .first()
    )
    if identifiers is None:
        return False
    with transaction.atomic():
        workshop = Workshop.objects.select_for_update().get(
            pk=identifiers["invitation__workshop_id"]
        )
        candidate = User.objects.select_for_update().get(
            pk=identifiers["invitation__user_id"]
        )
        invitation = UserInvitation.objects.select_for_update().get(pk=invitation_id)
        intent = EmailDeliveryIntent.objects.select_for_update().get(pk=intent_id)
        exact = (
            workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING
            and candidate.workshop_id == workshop.id
            and invitation.user_id == candidate.id
            and invitation.workshop_id == workshop.id
            and invitation.status == UserInvitation.Status.PENDING
            and invitation.invitation_generation == generation
            and intent.invitation_id == invitation.id
            and intent.invitation_generation == generation
            and intent.status == EmailDeliveryIntent.Status.PENDING
            and intent.attempt_count == 1
        )
        if not exact:
            return False
        intent.status = status
        intent.save(update_fields=("status",))
        return True


def send_invitation_generation(*, intent_id, invitation_id, generation, raw_token):
    discovered_recipient = (
        EmailDeliveryIntent.objects.filter(pk=intent_id)
        .values_list("recipient_email", flat=True)
        .first()
    )
    if discovered_recipient is None:
        return DeliveryResult(ResultCode.DELIVERY_NOOP)
    try:
        _validate_recipient_for_delivery(discovered_recipient)
    except Exception as error:
        logger.error(
            "Invitation delivery configuration rejected",
            extra={
                "operation": "identity.invitation.delivery.configure",
                "result_code": "failed",
                "exception_class": type(error).__name__,
                "intent_id": intent_id,
                "invitation_id": invitation_id,
            },
        )
        return DeliveryResult(ResultCode.DELIVERY_PENDING, intent_id)
    recipient = _claim_intent(
        intent_id=intent_id, invitation_id=invitation_id, generation=generation
    )
    if recipient is None:
        return DeliveryResult(ResultCode.DELIVERY_NOOP)
    link = (
        f"{settings.INVITATION_PUBLIC_ORIGIN}/invitations/{invitation_id}/{raw_token}"
    )
    try:
        _deliver(
            recipient=recipient,
            subject="Your Workshop invitation",
            body=f"Continue your Workshop invitation: {link}",
            from_email=settings.INVITATION_FROM_EMAIL,
        )
    except Exception:
        _record_outcome(
            intent_id=intent_id,
            invitation_id=invitation_id,
            generation=generation,
            status=EmailDeliveryIntent.Status.FAILED,
        )
        logger.error(
            "Invitation delivery failed",
            extra={
                "operation": "identity.invitation.deliver",
                "result_code": "failed",
                "intent_id": intent_id,
                "invitation_id": invitation_id,
            },
        )
        return DeliveryResult(ResultCode.DELIVERY_FAILED, intent_id)
    updated = _record_outcome(
        intent_id=intent_id,
        invitation_id=invitation_id,
        generation=generation,
        status=EmailDeliveryIntent.Status.SENT,
    )
    return DeliveryResult(
        ResultCode.DELIVERY_SENT if updated else ResultCode.DELIVERY_NOOP, intent_id
    )


def schedule_invitation_delivery(*, intent_id, invitation_id, generation, raw_token):
    try:
        send_invitation_generation(
            intent_id=intent_id,
            invitation_id=invitation_id,
            generation=generation,
            raw_token=raw_token,
        )
    except Exception as error:
        logger.error(
            "Invitation delivery scheduling failed",
            extra={
                "operation": "identity.invitation.schedule",
                "result_code": "failed",
                "exception_class": type(error).__name__,
                "intent_id": intent_id,
                "invitation_id": invitation_id,
            },
        )


def invitation_delivery_alerts(*, older_than=timedelta(minutes=15), now=None):
    cutoff = (now or timezone.now()) - older_than
    rows = EmailDeliveryIntent.objects.filter(
        status__in=(
            EmailDeliveryIntent.Status.PENDING,
            EmailDeliveryIntent.Status.FAILED,
            EmailDeliveryIntent.Status.SUPERSEDED,
        )
    ).filter(created_at__lte=cutoff)
    return tuple(
        {
            "intent_id": row["id"],
            "invitation_id": row["invitation_id"],
            "generation": row["invitation_generation"],
            "status": row["status"],
            "age_seconds": max(
                0, int(((now or timezone.now()) - row["created_at"]).total_seconds())
            ),
        }
        for row in rows.values(
            "id", "invitation_id", "invitation_generation", "status", "created_at"
        ).order_by("id")
    )
