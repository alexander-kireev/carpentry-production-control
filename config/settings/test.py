"""Test settings.

Enforces PostgreSQL for the test database — SQLite is never a valid target for
this project's correctness tests (CLAUDE.md Testing Rule; D-029).
"""

from .base import *  # noqa: F403

DEBUG = False

# Faster password hashing in tests only.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Hard guard: refuse to run the suite against anything but PostgreSQL.
if not DATABASES["default"]["ENGINE"].endswith("postgresql"):  # noqa: F405
    raise RuntimeError("Tests must run against PostgreSQL, never SQLite (D-029).")
