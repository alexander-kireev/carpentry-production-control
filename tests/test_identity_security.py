from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from identity.models import ActivationCodeAttemptBucket
from identity.security import (
    check_activation_code,
    client_ip_identity,
    normalize_authoritative_ip,
)

pytestmark = [pytest.mark.django_db(transaction=True)]
SECURITY = override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="security-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="security-hmac-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=7,
)


def test_ip_normalization_and_key_version_isolation():
    assert normalize_authoritative_ip("2001:0db8::1") == normalize_authoritative_ip(
        "2001:db8:0::1"
    )
    assert client_ip_identity("192.0.2.1", b"a", 1) != client_ip_identity(
        "192.0.2.1", b"b", 1
    )


@SECURITY
def test_first_five_failures_recorded_and_sixth_denied_before_comparison():
    for _ in range(5):
        assert not check_activation_code("wrong", "198.51.100.20")
    bucket = ActivationCodeAttemptBucket.objects.get()
    assert bucket.failed_attempt_count == 5
    assert not check_activation_code("security-code", "198.51.100.20")
    bucket.refresh_from_db()
    assert bucket.failed_attempt_count == 5


@SECURITY
def test_window_resets_at_equality_or_later():
    assert not check_activation_code("wrong", "198.51.100.21")
    bucket = ActivationCodeAttemptBucket.objects.get()
    boundary = timezone.now() - timedelta(minutes=15)
    ActivationCodeAttemptBucket.objects.filter(pk=bucket.pk).update(
        window_started_at=boundary, updated_at=boundary
    )
    assert not check_activation_code("wrong", "198.51.100.21")
    bucket.refresh_from_db()
    assert bucket.failed_attempt_count == 1
    assert bucket.window_started_at > boundary


@SECURITY
def test_window_is_active_just_before_boundary_and_resets_at_database_equality():
    from django.db import connection

    for _ in range(5):
        assert not check_activation_code("wrong", "198.51.100.22")
    bucket = ActivationCodeAttemptBucket.objects.get()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE activation_code_attempt_bucket "
            "SET window_started_at=statement_timestamp()-interval '14 minutes 59 seconds', "
            "updated_at=statement_timestamp() WHERE id=%s",
            [bucket.pk],
        )
    assert not check_activation_code("security-code", "198.51.100.22")
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE activation_code_attempt_bucket "
            "SET window_started_at=statement_timestamp()-interval '15 minutes', "
            "updated_at=statement_timestamp() WHERE id=%s",
            [bucket.pk],
        )
    assert check_activation_code("security-code", "198.51.100.22")


@pytest.mark.parametrize(
    "settings_values",
    [
        {
            "ADMIN_REGISTRATION_ACTIVATION_CODE": "",
            "ADMIN_REGISTRATION_IP_HMAC_KEY": "key",
            "ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION": 1,
        },
        {
            "ADMIN_REGISTRATION_ACTIVATION_CODE": "same",
            "ADMIN_REGISTRATION_IP_HMAC_KEY": "same",
            "ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION": 1,
        },
        {
            "ADMIN_REGISTRATION_ACTIVATION_CODE": "code",
            "ADMIN_REGISTRATION_IP_HMAC_KEY": "key",
            "ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION": 0,
        },
    ],
)
def test_bad_configuration_fails_closed(settings_values):
    with override_settings(**settings_values):
        assert not check_activation_code("code", "192.0.2.1")
    assert ActivationCodeAttemptBucket.objects.count() == 0


@SECURITY
def test_limiter_storage_failure_fails_closed(monkeypatch):
    from django.db import DatabaseError

    monkeypatch.setattr(
        "identity.security._advisory_lock",
        lambda digest: (_ for _ in ()).throw(
            DatabaseError("synthetic storage failure")
        ),
    )
    assert not check_activation_code("security-code", "192.0.2.90")
    assert ActivationCodeAttemptBucket.objects.count() == 0


@SECURITY
def test_http_uses_remote_addr_ignores_forwarded_headers_and_leaks_no_canaries(client):
    from identity.security import client_ip_identity

    response = client.post(
        "/register",
        {
            "submission_nonce": "canary-submission",
            "first_name": "Canary",
            "last_name": "Person",
            "date_of_birth": "1990-01-01",
            "email": "canary-email@example.test",
            "password": "Canary-Password-483!",
            "password_confirmation": "Canary-Password-483!",
            "activation_code": "canary-wrong-code",
        },
        REMOTE_ADDR="192.0.2.91",
        HTTP_X_FORWARDED_FOR="203.0.113.250",
        HTTP_FORWARDED="for=203.0.113.251",
    )
    assert response.status_code == 400
    body = response.content
    for canary in (
        b"canary-email@example.test",
        b"Canary-Password-483!",
        b"canary-wrong-code",
        b"192.0.2.91",
        b"203.0.113.250",
    ):
        assert canary not in body
    assert "Retry-After" not in response.headers
    _, expected_digest = client_ip_identity("192.0.2.91", b"security-hmac-key", 7)
    bucket = ActivationCodeAttemptBucket.objects.get()
    assert bytes(bucket.client_ip_hmac) == expected_digest
