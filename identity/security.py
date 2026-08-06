import hashlib
import hmac
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, connection, transaction

from .models import ActivationCodeAttemptBucket

WINDOW = timedelta(minutes=15)
MAX_FAILED_ATTEMPTS = 5


class SecurityGateUnavailable(Exception):
    pass


def registration_security_configuration():
    code = settings.ADMIN_REGISTRATION_ACTIVATION_CODE
    key = settings.ADMIN_REGISTRATION_IP_HMAC_KEY
    version = settings.ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION
    if (
        not code
        or not key
        or isinstance(version, bool)
        or not isinstance(version, int)
        or not 0 < version <= 32767
    ):
        raise SecurityGateUnavailable
    if hmac.compare_digest(code.encode(), key.encode()):
        raise SecurityGateUnavailable
    return code, key.encode(), version


def normalize_authoritative_ip(remote_addr):
    try:
        address = ipaddress.ip_address((remote_addr or "").strip())
    except ValueError as error:
        raise SecurityGateUnavailable from error
    return address.packed


def client_ip_identity(remote_addr, key, version):
    return version, hmac.new(
        key, normalize_authoritative_ip(remote_addr), hashlib.sha256
    ).digest()


def _database_now():
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


def _advisory_lock(digest):
    lock_id = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def check_activation_code(submitted_code, remote_addr):
    """Apply precheck, comparison and invalid-attempt recording in that order."""
    try:
        expected, key, version = registration_security_configuration()
        version, digest = client_ip_identity(remote_addr, key, version)
        with transaction.atomic():
            _advisory_lock(digest)
            now = _database_now()
            bucket = (
                ActivationCodeAttemptBucket.objects.select_for_update()
                .filter(hmac_key_version=version, client_ip_hmac=digest)
                .first()
            )
            active = bucket is not None and now < bucket.window_started_at + WINDOW
            if active and bucket.failed_attempt_count >= MAX_FAILED_ATTEMPTS:
                return False

            if hmac.compare_digest((submitted_code or "").encode(), expected.encode()):
                return True

            if not active:
                if bucket is None:
                    bucket = ActivationCodeAttemptBucket(
                        hmac_key_version=version,
                        client_ip_hmac=digest,
                    )
                bucket.window_started_at = now
                bucket.failed_attempt_count = 1
            else:
                bucket.failed_attempt_count += 1
            bucket.updated_at = now
            bucket.save()
            return False
    except DatabaseError, SecurityGateUnavailable, UnicodeError:
        return False


def registration_payload_fingerprint(*, first_name, last_name, date_of_birth, email):
    fields = (
        first_name.strip(),
        last_name.strip(),
        date_of_birth.isoformat(),
        email.strip().casefold(),
    )
    encoded = b"".join(
        len(value.encode()).to_bytes(4, "big") + value.encode() for value in fields
    )
    return hashlib.sha256(encoded).digest()
