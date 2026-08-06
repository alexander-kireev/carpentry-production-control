from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from identity import delivery
from identity.commands import invite_permanent_manager
from identity.models import EmailDeliveryIntent
from tests.test_manager_invitation import attached_admin, payload

pytestmark = pytest.mark.django_db(transaction=True)


def test_monitor_is_safe_read_only_and_never_sends(monkeypatch, settings):
    settings.INVITATION_DELIVERY_MODE = "failing"
    admin, _, _ = attached_admin()
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="monitor"
    )
    intent = EmailDeliveryIntent.objects.get()
    before = tuple(EmailDeliveryIntent.objects.values())
    monkeypatch.setattr(
        delivery, "_deliver", lambda **kwargs: pytest.fail("monitor sent")
    )
    output = StringIO()
    call_command("monitor_invitation_delivery", older_than_minutes=0, stdout=output)
    text = output.getvalue()
    assert f"intent_id={intent.id}" in text and "status=failed" in text
    assert "example.test" not in text and "/invitations/" not in text
    assert tuple(EmailDeliveryIntent.objects.values()) == before
    rows = delivery.invitation_delivery_alerts(
        older_than=timedelta(0), now=timezone.now()
    )
    assert rows[0]["status"] == "failed"
