import json
import logging
import os
import subprocess
import sys

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.logging import JsonFormatter
from config.settings import _parse_bool, _parse_hosts, _parse_port, _required


def test_required_setting_rejects_missing_value(monkeypatch):
    monkeypatch.delenv("A2_REQUIRED_SETTING", raising=False)

    with pytest.raises(ImproperlyConfigured, match="A2_REQUIRED_SETTING"):
        _required("A2_REQUIRED_SETTING")


@pytest.mark.parametrize("value", ["", " ", "yes", "1"])
def test_boolean_setting_rejects_non_boolean_values(monkeypatch, value):
    monkeypatch.setenv("A2_BOOLEAN_SETTING", value)

    with pytest.raises(ImproperlyConfigured, match="A2_BOOLEAN_SETTING"):
        _parse_bool("A2_BOOLEAN_SETTING")


def test_host_setting_rejects_empty_list(monkeypatch):
    monkeypatch.setenv("A2_HOST_SETTING", " , ")

    with pytest.raises(ImproperlyConfigured, match="A2_HOST_SETTING"):
        _parse_hosts("A2_HOST_SETTING")


@pytest.mark.parametrize("value", ["not-a-port", "0", "65536"])
def test_port_setting_rejects_invalid_value(monkeypatch, value):
    monkeypatch.setenv("A2_PORT_SETTING", value)

    with pytest.raises(ImproperlyConfigured, match="A2_PORT_SETTING"):
        _parse_port("A2_PORT_SETTING")


def test_missing_secret_fails_without_disclosing_configured_value():
    environment = os.environ.copy()
    configured_secret = environment.pop("DJANGO_SECRET_KEY")

    result = subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr
    assert configured_secret not in result.stderr
    assert "DATABASE_PASSWORD" not in result.stderr


def test_sb01_apps_and_custom_user_are_configured():
    assert {
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "identity.apps.IdentityConfig",
        "workshops.apps.WorkshopsConfig",
    }.issubset(settings.INSTALLED_APPS)
    assert {"django.contrib.admin", "django.contrib.messages"}.isdisjoint(
        settings.INSTALLED_APPS
    )
    assert settings.AUTH_USER_MODEL == "identity.User"
    assert settings.AUTHENTICATION_BACKENDS == ["identity.backends.EmailBackend"]
    assert settings.SILENCED_SYSTEM_CHECKS == ["auth.W004"]


def test_session_and_authentication_middleware_are_ordered():
    session_index = settings.MIDDLEWARE.index(
        "django.contrib.sessions.middleware.SessionMiddleware"
    )
    authentication_index = settings.MIDDLEWARE.index(
        "django.contrib.auth.middleware.AuthenticationMiddleware"
    )
    assert session_index < authentication_index
    csrf_index = settings.MIDDLEWARE.index("django.middleware.csrf.CsrfViewMiddleware")
    guard_index = settings.MIDDLEWARE.index(
        "identity.middleware.PreWorkshopAccessMiddleware"
    )
    assert session_index < csrf_index < authentication_index < guard_index


def test_registration_security_settings_default_fail_closed():
    assert hasattr(settings, "ADMIN_REGISTRATION_ACTIVATION_CODE")
    assert hasattr(settings, "ADMIN_REGISTRATION_IP_HMAC_KEY")
    assert hasattr(settings, "ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION")


def test_canonical_password_validators_are_configured():
    validators = settings.AUTH_PASSWORD_VALIDATORS
    names = [item["NAME"].rsplit(".", 1)[-1] for item in validators]
    assert names == [
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    ]
    assert validators[1]["OPTIONS"] == {"min_length": 10}


def test_json_formatter_uses_allowlisted_fields_only():
    record = logging.LogRecord(
        name="foundation",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Fixed diagnostic",
        args=(),
        exc_info=None,
    )
    record.operation = "foundation.test"
    record.result_code = "ok"
    record.password = "must-not-appear"

    payload = json.loads(JsonFormatter().format(record))

    assert payload.keys() == {
        "timestamp",
        "level",
        "logger",
        "message",
        "operation",
        "result_code",
    }
    assert "must-not-appear" not in json.dumps(payload)


def test_json_formatter_includes_exception_class():
    error = ValueError("diagnostic detail")
    record = logging.LogRecord(
        name="foundation",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Foundation diagnostic failed",
        args=(),
        exc_info=(ValueError, error, None),
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["exception_class"] == "ValueError"


def _live_environment(**overrides):
    environment = os.environ.copy()
    environment.update(
        {
            "INVITATION_DELIVERY_MODE": "live",
            "INVITATION_ENVIRONMENT": "test",
            "INVITATION_PUBLIC_ORIGIN": "https://qa.alder-and-green.co.uk",
            "INVITATION_SMTP_HOST": "smtp.resend.com",
            "INVITATION_SMTP_PORT": "587",
            "INVITATION_SMTP_USERNAME": "resend",
            "INVITATION_FROM_EMAIL": "workshop@alder-and-green.co.uk",
            "INVITATION_SMTP_API_KEY": "synthetic-api-key-canary",
            "INVITATION_SMTP_TIMEOUT_SECONDS": "10",
            "INVITATION_RECIPIENT_ALLOWLIST": "manager@example.test",
        }
    )
    environment.update(overrides)
    return environment


def _import_settings(environment):
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def test_memory_invitation_mode_requires_no_live_secret():
    environment = _live_environment(
        INVITATION_DELIVERY_MODE="memory", INVITATION_SMTP_API_KEY=""
    )
    assert _import_settings(environment).returncode == 0


@pytest.mark.parametrize(
    "override",
    (
        {"INVITATION_DELIVERY_MODE": "automatic"},
        {"INVITATION_SMTP_HOST": "smtp.example.test"},
        {"INVITATION_SMTP_PORT": "25"},
        {"INVITATION_SMTP_USERNAME": "other"},
        {"INVITATION_FROM_EMAIL": "other@alder-and-green.co.uk"},
        {"INVITATION_SMTP_API_KEY": ""},
        {"INVITATION_SMTP_TIMEOUT_SECONDS": "30"},
        {"INVITATION_RECIPIENT_ALLOWLIST": ""},
    ),
)
def test_live_invitation_mode_rejects_inexact_configuration_without_secret_leak(
    override,
):
    result = _import_settings(_live_environment(**override))
    assert result.returncode != 0
    assert "synthetic-api-key-canary" not in result.stderr


@pytest.mark.parametrize(
    "origin",
    (
        "http://workshop.alder-and-green.co.uk",
        "https://localhost",
        "https://127.0.0.1",
        "https://user:password@workshop.alder-and-green.co.uk",
        "https://workshop.alder-and-green.co.uk/path",
        "https://workshop.alder-and-green.co.uk?query=yes",
    ),
)
def test_production_live_invitation_requires_public_credential_free_https_origin(
    origin,
):
    result = _import_settings(
        _live_environment(
            INVITATION_ENVIRONMENT="production",
            INVITATION_PUBLIC_ORIGIN=origin,
            INVITATION_RECIPIENT_ALLOWLIST="",
        )
    )
    assert result.returncode != 0
    assert "synthetic-api-key-canary" not in result.stderr


def test_production_live_invitation_accepts_public_https_origin():
    result = _import_settings(
        _live_environment(
            INVITATION_ENVIRONMENT="production",
            INVITATION_PUBLIC_ORIGIN="https://workshop.alder-and-green.co.uk",
            INVITATION_RECIPIENT_ALLOWLIST="",
        )
    )
    assert result.returncode == 0


def test_event_app_and_secret_safe_logger_are_configured():
    from django.conf import settings

    assert "events.apps.EventsConfig" in settings.INSTALLED_APPS
    assert settings.LOGGING["loggers"]["events"]["propagate"] is False
