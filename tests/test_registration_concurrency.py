from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, current_thread

import pytest
from django.db import close_old_connections
from django.test import override_settings

from identity.commands import register_administrator
from identity.models import (
    ActivationCodeAttemptBucket,
    RegistrationCommandReceipt,
    User,
)
from identity.security import check_activation_code

pytestmark = pytest.mark.django_db(transaction=True)


@override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="concurrency-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="concurrency-hmac-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=3,
)
def test_concurrent_absent_bucket_failures_are_serialized(monkeypatch):
    barrier = Barrier(5)
    reached = []
    reached_lock = Lock()
    from identity import security

    original_lock = security._advisory_lock

    def synchronized_lock(digest):
        with reached_lock:
            reached.append(current_thread().name)
        barrier.wait(timeout=10)
        return original_lock(digest)

    monkeypatch.setattr(security, "_advisory_lock", synchronized_lock)

    def attempt(_):
        close_old_connections()
        try:
            return check_activation_code("wrong", "203.0.113.50")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert list(pool.map(attempt, range(5))) == [False] * 5
    assert len(reached) == 5
    assert ActivationCodeAttemptBucket.objects.get().failed_attempt_count == 5


@override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="concurrency-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="concurrency-hmac-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=3,
)
def test_concurrent_same_key_and_email_commit_one_result(monkeypatch):
    barrier = Barrier(2)
    reached = []
    reached_lock = Lock()
    original_save = User.save

    def synchronized_save(self, *args, **kwargs):
        with reached_lock:
            reached.append(current_thread().name)
        barrier.wait(timeout=10)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(User, "save", synchronized_save)
    data = {
        "submission_nonce": "concurrent-submission",
        "first_name": "Concurrent",
        "last_name": "Admin",
        "date_of_birth": "1990-01-01",
        "email": "race@example.test",
        "password": "Concurrency-Password-483!",
        "password_confirmation": "Concurrency-Password-483!",
        "activation_code": "concurrency-code",
    }

    def attempt(number):
        close_old_connections()
        try:
            return register_administrator(
                data=data,
                remote_addr=f"203.0.113.{number + 60}",
                idempotency_key="same-race-key",
            ).code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert len(reached) == 2
    assert results.count("success") == 2
    assert User.objects.count() == RegistrationCommandReceipt.objects.count() == 1


@override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="concurrency-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="concurrency-hmac-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=3,
)
def test_concurrent_case_variant_email_with_different_keys_has_one_winner(monkeypatch):
    barrier = Barrier(2)
    reached = []
    reached_lock = Lock()
    original_save = User.save

    def synchronized_save(self, *args, **kwargs):
        with reached_lock:
            reached.append(current_thread().name)
        barrier.wait(timeout=10)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(User, "save", synchronized_save)
    base = {
        "submission_nonce": "concurrent-email",
        "first_name": "Email",
        "last_name": "Race",
        "date_of_birth": "1990-01-01",
        "password": "Concurrency-Password-483!",
        "password_confirmation": "Concurrency-Password-483!",
        "activation_code": "concurrency-code",
    }

    def attempt(number):
        close_old_connections()
        try:
            return register_administrator(
                data={
                    **base,
                    "email": ("RACE2" if number else "race2") + "@example.test",
                },
                remote_addr=f"203.0.113.{number + 70}",
                idempotency_key=f"email-race-{number}",
            ).code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert len(reached) == 2
    assert results.count("success") == 1
    assert results.count("registration_unavailable") == 1
    assert User.objects.count() == RegistrationCommandReceipt.objects.count() == 1


@override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="concurrency-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="concurrency-hmac-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=3,
)
def test_concurrent_fifth_and_sixth_never_store_six(monkeypatch):
    barrier = Barrier(2)
    reached = []
    reached_lock = Lock()
    from identity import security

    original_lock = security._advisory_lock

    def synchronized_lock(digest):
        with reached_lock:
            reached.append(current_thread().name)
        barrier.wait(timeout=10)
        return original_lock(digest)

    for _ in range(4):
        assert not check_activation_code("wrong", "203.0.113.80")
    monkeypatch.setattr(security, "_advisory_lock", synchronized_lock)

    def attempt(code):
        close_old_connections()
        try:
            return check_activation_code(code, "203.0.113.80")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["wrong", "wrong-again"]))
    assert results == [False, False]
    assert len(reached) == 2
    assert ActivationCodeAttemptBucket.objects.get().failed_attempt_count == 5
