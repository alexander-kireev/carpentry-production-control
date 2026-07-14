"""Test factories.

A minimal ``UserFactory`` (F2) — the first factory in the harness. Later slices
extend it as the ``User`` model gains domain fields (D0-1).
"""

import factory

from accounts.models import User

# Known password so tests can authenticate the built users.
DEFAULT_PASSWORD = "workshop-pass-123"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    account_role = User.AccountRole.TECHNICIAN
    password = factory.django.Password(DEFAULT_PASSWORD)
