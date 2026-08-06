"""Environment-driven settings for the local Django foundation."""

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _required(name):
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ImproperlyConfigured(f"Required setting {name} is missing or empty")
    return value


def _parse_bool(name):
    value = _required(name).strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ImproperlyConfigured(f"Required setting {name} must be true or false")


def _parse_hosts(name):
    hosts = [host.strip() for host in _required(name).split(",")]
    if not hosts or any(not host for host in hosts):
        raise ImproperlyConfigured(
            f"Required setting {name} must contain a comma-separated host list"
        )
    return hosts


def _parse_port(name):
    value = _required(name).strip()
    try:
        port = int(value)
    except ValueError as error:
        raise ImproperlyConfigured(
            f"Required setting {name} must be an integer port"
        ) from error
    if not 1 <= port <= 65535:
        raise ImproperlyConfigured(
            f"Required setting {name} must be between 1 and 65535"
        )
    return port


def _optional_positive_int(name):
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if 0 < parsed <= 32767 else None


SECRET_KEY = _required("DJANGO_SECRET_KEY")
DEBUG = _parse_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = _parse_hosts("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "foundation.apps.FoundationConfig",
    "workshops.apps.WorkshopsConfig",
    "identity.apps.IdentityConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "identity.middleware.PreWorkshopAccessMiddleware",
    "django.middleware.common.CommonMiddleware",
]

AUTH_USER_MODEL = "identity.User"
AUTHENTICATION_BACKENDS = ["identity.backends.EmailBackend"]
SILENCED_SYSTEM_CHECKS = ["auth.W004"]

ADMIN_REGISTRATION_ACTIVATION_CODE = os.environ.get(
    "ADMIN_REGISTRATION_ACTIVATION_CODE", ""
).strip()
ADMIN_REGISTRATION_IP_HMAC_KEY = os.environ.get(
    "ADMIN_REGISTRATION_IP_HMAC_KEY", ""
).strip()
ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION = _optional_positive_int(
    "ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION"
)

INVITATION_DELIVERY_MODE = (
    os.environ.get("INVITATION_DELIVERY_MODE", "memory").strip().lower()
)
if INVITATION_DELIVERY_MODE not in {"memory", "failing", "live"}:
    raise ImproperlyConfigured(
        "INVITATION_DELIVERY_MODE must be memory, failing or live"
    )
INVITATION_ENVIRONMENT = (
    os.environ.get("INVITATION_ENVIRONMENT", "local").strip().lower()
)
if INVITATION_ENVIRONMENT not in {"local", "test", "production"}:
    raise ImproperlyConfigured(
        "INVITATION_ENVIRONMENT must be local, test or production"
    )
INVITATION_PUBLIC_ORIGIN = (
    os.environ.get("INVITATION_PUBLIC_ORIGIN", "http://127.0.0.1:8000")
    .strip()
    .rstrip("/")
)
if not INVITATION_PUBLIC_ORIGIN.startswith(("http://", "https://")):
    raise ImproperlyConfigured("INVITATION_PUBLIC_ORIGIN must be an http(s) origin")
INVITATION_SMTP_HOST = os.environ.get("INVITATION_SMTP_HOST", "smtp.resend.com").strip()
INVITATION_SMTP_PORT = os.environ.get("INVITATION_SMTP_PORT", "587").strip()
INVITATION_SMTP_USERNAME = os.environ.get("INVITATION_SMTP_USERNAME", "resend").strip()
INVITATION_FROM_EMAIL = (
    os.environ.get("INVITATION_FROM_EMAIL", "workshop@alder-and-green.co.uk")
    .strip()
    .casefold()
)
INVITATION_SMTP_API_KEY = os.environ.get("INVITATION_SMTP_API_KEY", "").strip()
INVITATION_SMTP_TIMEOUT_SECONDS = os.environ.get(
    "INVITATION_SMTP_TIMEOUT_SECONDS", "10"
).strip()
INVITATION_RECIPIENT_ALLOWLIST = tuple(
    value.strip().casefold()
    for value in os.environ.get("INVITATION_RECIPIENT_ALLOWLIST", "").split(",")
    if value.strip()
)


def _validate_live_invitation_delivery():
    if INVITATION_DELIVERY_MODE != "live":
        return
    exact = (
        INVITATION_SMTP_HOST == "smtp.resend.com"
        and INVITATION_SMTP_PORT == "587"
        and INVITATION_SMTP_USERNAME == "resend"
        and INVITATION_FROM_EMAIL == "workshop@alder-and-green.co.uk"
        and INVITATION_SMTP_TIMEOUT_SECONDS == "10"
        and bool(INVITATION_SMTP_API_KEY)
    )
    if not exact:
        raise ImproperlyConfigured("Live invitation SMTP configuration is invalid")
    parsed = urlsplit(INVITATION_PUBLIC_ORIGIN)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ImproperlyConfigured("INVITATION_PUBLIC_ORIGIN is invalid")
    if INVITATION_ENVIRONMENT != "production":
        if not INVITATION_RECIPIENT_ALLOWLIST:
            raise ImproperlyConfigured(
                "Live non-production delivery requires INVITATION_RECIPIENT_ALLOWLIST"
            )
        return
    if parsed.scheme != "https":
        raise ImproperlyConfigured("Production invitation origin must use HTTPS")
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ImproperlyConfigured("Production invitation origin must be public")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ImproperlyConfigured("Production invitation origin must be public")


_validate_live_invitation_delivery()

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _required("DATABASE_NAME"),
        "USER": _required("DATABASE_USER"),
        "PASSWORD": _required("DATABASE_PASSWORD"),
        "HOST": _required("DATABASE_HOST"),
        "PORT": _parse_port("DATABASE_PORT"),
    }
}

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "config.logging.JsonFormatter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        }
    },
    "loggers": {
        "foundation": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "identity": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
