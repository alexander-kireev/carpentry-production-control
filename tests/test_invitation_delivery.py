import pytest
from django.db import connection

from identity import delivery
from identity.commands import invite_permanent_manager
from identity.models import EmailDeliveryIntent
from identity.results import ResultCode
from tests.test_manager_invitation import attached_admin, payload

pytestmark = pytest.mark.django_db(transaction=True)


def test_provider_runs_after_claim_commit_and_only_once(monkeypatch):
    calls = []

    def provider(**message):
        assert not connection.in_atomic_block
        calls.append(message)

    monkeypatch.setattr(delivery, "_deliver", provider)
    admin, _, _ = attached_admin()
    result = invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="delivery"
    )
    assert len(calls) == 1
    assert "/invitations/" in calls[0]["body"]
    intent = EmailDeliveryIntent.objects.get()
    second = delivery.send_invitation_generation(
        intent_id=intent.id,
        invitation_id=result.invitation.id,
        generation=1,
        raw_token="not-current",
    )
    assert second.code == ResultCode.DELIVERY_NOOP and len(calls) == 1


@pytest.mark.parametrize(
    "backend,expected", (("failing", "failed"), ("memory", "sent"))
)
def test_delivery_failure_preserves_source_truth(settings, backend, expected):
    settings.INVITATION_DELIVERY_MODE = backend
    admin, workshop, _ = attached_admin()
    invite_permanent_manager(actor_id=admin.id, data=payload(), idempotency_key=backend)
    workshop.refresh_from_db()
    intent = EmailDeliveryIntent.objects.get()
    assert workshop.status == "manager_activation_pending"
    assert intent.status == expected and intent.attempt_count == 1


def test_stale_callback_changes_zero_rows(monkeypatch):
    monkeypatch.setattr(delivery, "_deliver", lambda **kwargs: None)
    admin, _, _ = attached_admin()
    result = invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="callback"
    )
    intent = EmailDeliveryIntent.objects.get()
    assert not delivery._record_outcome(
        intent_id=intent.id,
        invitation_id=result.invitation.id,
        generation=2,
        status="failed",
    )
    intent.refresh_from_db()
    assert intent.status == "sent"


class SMTPDouble:
    instances = []

    def __init__(self, host, port, timeout):
        self.calls = [("connect", host, port, timeout)]
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.calls.append(("close",))

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self, *, context):
        self.calls.append(("starttls", context.check_hostname, context.verify_mode))

    def login(self, username, password):
        self.calls.append(("login", username, bool(password)))

    def send_message(self, message):
        self.calls.append(("send", message["From"], message["To"], message["Subject"]))
        return {}


def _live_settings(settings, *, allowlist=("manager@example.test",)):
    settings.INVITATION_DELIVERY_MODE = "live"
    settings.INVITATION_ENVIRONMENT = "test"
    settings.INVITATION_PUBLIC_ORIGIN = "https://qa.alder-and-green.co.uk"
    settings.INVITATION_SMTP_HOST = "smtp.resend.com"
    settings.INVITATION_SMTP_PORT = "587"
    settings.INVITATION_SMTP_USERNAME = "resend"
    settings.INVITATION_FROM_EMAIL = "workshop@alder-and-green.co.uk"
    settings.INVITATION_SMTP_API_KEY = "synthetic-api-key"
    settings.INVITATION_SMTP_TIMEOUT_SECONDS = "10"
    settings.INVITATION_RECIPIENT_ALLOWLIST = allowlist


def test_live_smtp_uses_verified_starttls_auth_and_provider_acceptance(
    monkeypatch, settings
):
    _live_settings(settings)
    SMTPDouble.instances.clear()
    monkeypatch.setattr(delivery.smtplib, "SMTP", SMTPDouble)
    admin, _, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="live-smtp"
    )
    intent = EmailDeliveryIntent.objects.get()
    assert intent.status == "sent" and intent.attempt_count == 1
    calls = SMTPDouble.instances[0].calls
    assert calls[0] == ("connect", "smtp.resend.com", 587, 10)
    assert calls[1] == ("ehlo",)
    assert calls[2][0:2] == ("starttls", True)
    assert calls[2][2] == delivery.ssl.CERT_REQUIRED
    assert calls[3] == ("ehlo",)
    assert calls[4] == ("login", "resend", True)
    assert calls[5] == (
        "send",
        "workshop@alder-and-green.co.uk",
        "manager@example.test",
        "Your Workshop invitation",
    )


def test_memory_mode_never_opens_socket(monkeypatch, settings):
    settings.INVITATION_DELIVERY_MODE = "memory"
    monkeypatch.setattr(
        delivery.smtplib,
        "SMTP",
        lambda *args, **kwargs: pytest.fail("memory mode opened SMTP"),
    )
    admin, _, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="memory-no-network"
    )
    assert EmailDeliveryIntent.objects.get().status == "sent"


def test_nonproduction_allowlist_rejects_before_claim_or_socket(monkeypatch, settings):
    _live_settings(settings, allowlist=("nominated@example.test",))
    monkeypatch.setattr(
        delivery.smtplib,
        "SMTP",
        lambda *args, **kwargs: pytest.fail("unallowlisted delivery opened SMTP"),
    )
    admin, workshop, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="not-allowlisted"
    )
    intent = EmailDeliveryIntent.objects.get()
    workshop.refresh_from_db()
    assert (intent.status, intent.attempt_count, intent.last_attempted_at) == (
        "pending",
        0,
        None,
    )
    assert workshop.status == "manager_activation_pending"


@pytest.mark.parametrize(
    "failure_method", ("ehlo", "starttls", "login", "send_message")
)
def test_live_smtp_failure_records_failed_without_retry(
    monkeypatch, settings, failure_method
):
    _live_settings(settings)

    class FailingSMTP(SMTPDouble):
        instances = []

        def __getattribute__(self, name):
            if name == failure_method:

                def fail(*args, **kwargs):
                    raise TimeoutError("synthetic provider failure")

                return fail
            return super().__getattribute__(name)

    monkeypatch.setattr(delivery.smtplib, "SMTP", FailingSMTP)
    admin, workshop, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key=failure_method
    )
    intent = EmailDeliveryIntent.objects.get()
    workshop.refresh_from_db()
    assert intent.status == "failed" and intent.attempt_count == 1
    assert workshop.status == "manager_activation_pending"
    assert len(FailingSMTP.instances) == 1
    replay = invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key=failure_method
    )
    assert replay.code == ResultCode.REPLAY
    assert len(FailingSMTP.instances) == 1


def test_live_smtp_connect_failure_records_failed(monkeypatch, settings):
    _live_settings(settings)
    monkeypatch.setattr(
        delivery.smtplib,
        "SMTP",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("synthetic DNS/connect failure")
        ),
    )
    admin, _, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="connect-failure"
    )
    intent = EmailDeliveryIntent.objects.get()
    assert intent.status == "failed" and intent.attempt_count == 1


def test_live_smtp_recipient_rejection_is_not_sent(monkeypatch, settings):
    _live_settings(settings)

    class RejectingSMTP(SMTPDouble):
        instances = []

        def send_message(self, message):
            return {"manager@example.test": (550, b"synthetic rejection")}

    monkeypatch.setattr(delivery.smtplib, "SMTP", RejectingSMTP)
    admin, _, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="recipient-rejection"
    )
    intent = EmailDeliveryIntent.objects.get()
    assert intent.status == "failed" and intent.attempt_count == 1
