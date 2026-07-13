"""Custom-user assertions: email login identity + account_role enum."""

import pytest
from django.contrib.auth import authenticate, get_user_model

from accounts.models import User

pytestmark = pytest.mark.django_db


def test_auth_user_model_is_accounts_user():
    assert get_user_model() is User


def test_username_field_is_email():
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []


def test_username_is_not_a_field():
    field_names = {field.name for field in User._meta.get_fields()}
    assert "username" not in field_names


def test_account_role_offers_exactly_four_lowercase_choices():
    values = [value for value, _label in User._meta.get_field("account_role").choices]
    assert values == ["admin", "manager", "operator", "technician"]


def test_user_created_and_authenticated_by_email():
    user = User.objects.create_user(
        email="tech@example.com",
        password="workshop-pass-123",
        account_role=User.AccountRole.TECHNICIAN,
    )
    assert user.account_role == "technician"
    assert user.check_password("workshop-pass-123")

    authenticated = authenticate(username="tech@example.com", password="workshop-pass-123")
    assert authenticated == user


def test_superuser_has_staff_and_superuser_flags():
    admin = User.objects.create_superuser(
        email="admin@example.com",
        password="workshop-pass-123",
        account_role=User.AccountRole.ADMIN,
    )
    assert admin.is_staff is True
    assert admin.is_superuser is True
