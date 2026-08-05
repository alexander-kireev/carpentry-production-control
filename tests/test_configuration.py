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


def test_default_contrib_apps_are_not_installed():
    forbidden_apps = {
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
    }

    assert forbidden_apps.isdisjoint(settings.INSTALLED_APPS)


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
