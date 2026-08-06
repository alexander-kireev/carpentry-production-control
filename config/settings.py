"""Environment-driven settings for the local Django foundation."""

import os
from pathlib import Path

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
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.common.CommonMiddleware",
]

AUTH_USER_MODEL = "identity.User"
AUTHENTICATION_BACKENDS = []
SILENCED_SYSTEM_CHECKS = ["auth.W004"]

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
        }
    },
}
