import pytest
from django.test import override_settings

from identity.commands import register_administrator
from identity.models import (
    ActivationCodeAttemptBucket,
    RegistrationCommandReceipt,
    User,
)
from identity.results import ResultCode

pytestmark = [pytest.mark.django_db(transaction=True)]
SECURITY = override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="correct-development-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="independent-limiter-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=1,
)


def payload(**overrides):
    values = {
        "first_name": "Ada",
        "submission_nonce": "stable-browser-submission",
        "last_name": "Lovelace",
        "date_of_birth": "1990-01-02",
        "email": "Ada@example.test",
        "password": "Turbine-Workshop-483!",
        "password_confirmation": "Turbine-Workshop-483!",
        "activation_code": "correct-development-code",
    }
    values.update(overrides)
    return values


@SECURITY
def test_registration_creates_exact_user_and_receipt_only():
    result = register_administrator(
        data=payload(), remote_addr="192.0.2.10", idempotency_key="command-1"
    )
    assert result.code == ResultCode.SUCCESS
    user = User.objects.get()
    assert (
        user.email,
        user.account_role,
        user.status,
        user.onboarding_state,
        user.workshop_id,
        user.workshop_role_id,
        user.version,
    ) == (
        "ada@example.test",
        "admin",
        "active",
        "registered_no_workshop",
        None,
        None,
        1,
    )
    assert user.check_password(payload()["password"])
    receipt = RegistrationCommandReceipt.objects.get()
    assert receipt.result_user == user
    assert bytes(receipt.payload_fingerprint) not in {
        payload()["password"].encode(),
        payload()["activation_code"].encode(),
    }


@SECURITY
def test_correct_code_precedes_ordinary_validation_and_does_not_consume_allowance():
    result = register_administrator(
        data=payload(email="not-an-email"),
        remote_addr="192.0.2.11",
        idempotency_key="command-2",
    )
    assert result.code == ResultCode.VALIDATION_ERROR
    assert ActivationCodeAttemptBucket.objects.count() == 0
    assert User.objects.count() == 0


@SECURITY
def test_invalid_code_hides_ordinary_validation():
    result = register_administrator(
        data=payload(email="not-an-email", activation_code="wrong"),
        remote_addr="192.0.2.12",
        idempotency_key="command-3",
    )
    assert result.code == ResultCode.REGISTRATION_UNAVAILABLE
    assert not result.errors
    assert ActivationCodeAttemptBucket.objects.get().failed_attempt_count == 1


@SECURITY
def test_same_key_replay_requires_same_payload_and_password():
    first = register_administrator(
        data=payload(), remote_addr="192.0.2.13", idempotency_key="replay"
    )
    replay = register_administrator(
        data=payload(email="ADA@EXAMPLE.TEST"),
        remote_addr="192.0.2.13",
        idempotency_key="replay",
    )
    wrong_password = register_administrator(
        data=payload(
            password="Different-valid-password-539!",
            password_confirmation="Different-valid-password-539!",
        ),
        remote_addr="192.0.2.13",
        idempotency_key="replay",
    )
    misuse = register_administrator(
        data=payload(last_name="Byron"),
        remote_addr="192.0.2.13",
        idempotency_key="replay",
    )
    assert first.succeeded and replay.succeeded
    assert wrong_password.code == misuse.code == ResultCode.REGISTRATION_UNAVAILABLE
    assert User.objects.count() == RegistrationCommandReceipt.objects.count() == 1


@SECURITY
def test_duplicate_email_new_key_is_generic():
    assert register_administrator(
        data=payload(), remote_addr="192.0.2.14", idempotency_key="one"
    ).succeeded
    result = register_administrator(
        data=payload(), remote_addr="192.0.2.14", idempotency_key="two"
    )
    assert result.code == ResultCode.REGISTRATION_UNAVAILABLE
    assert User.objects.count() == RegistrationCommandReceipt.objects.count() == 1


@SECURITY
def test_failure_between_user_and_receipt_rolls_back_both(monkeypatch):
    from django.db import IntegrityError

    def fail_receipt(*args, **kwargs):
        raise IntegrityError("synthetic receipt failure")

    monkeypatch.setattr(RegistrationCommandReceipt.objects, "create", fail_receipt)
    result = register_administrator(
        data=payload(), remote_addr="192.0.2.15", idempotency_key="rollback"
    )
    assert result.code == ResultCode.REGISTRATION_UNAVAILABLE
    assert User.objects.count() == RegistrationCommandReceipt.objects.count() == 0
