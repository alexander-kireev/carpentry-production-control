"""Custom-user assertions: email login identity, account_role enum, and the
D0-2 domain fields (workshop / workshop_role / status / phone / date_of_birth /
clearances)."""

import datetime

import pytest
from django.contrib.auth import authenticate, get_user_model

from accounts.models import User
from tests.factories import OperationTypeFactory, UserFactory

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
        date_of_birth=datetime.date(1990, 1, 1),
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


def test_create_superuser_supplies_debug_defaults():
    # createsuperuser collects neither field; create_superuser supplies
    # placeholders so the CLI yields a usable debug account. Not the real admin
    # path — A1 self-registration captures a real DOB — hence the placeholders.
    admin = User.objects.create_superuser(
        email="root@example.com",
        password="workshop-pass-123",
    )
    assert admin.is_staff is True
    assert admin.is_superuser is True
    assert admin.account_role == User.AccountRole.ADMIN == "admin"
    assert admin.date_of_birth == datetime.date(2000, 1, 1)
    assert admin.pk is not None


def test_user_domain_fields_present():
    field_names = {field.name for field in User._meta.get_fields()}
    assert {
        "workshop",
        "workshop_role",
        "status",
        "phone",
        "date_of_birth",
        "clearances",
    } <= field_names


def test_status_offers_four_choices_and_defaults_to_pending():
    values = [value for value, _label in User._meta.get_field("status").choices]
    assert values == ["pending", "active", "on_leave", "inactive"]
    assert UserFactory().status == User.Status.PENDING == "pending"


def test_workshop_is_nullable_and_defaults_null():
    # The admin exists before a workshop (decision 2): a user with no workshop
    # is valid and saveable.
    user = UserFactory()
    assert user.workshop is None
    assert User._meta.get_field("workshop").null is True


def test_workshop_role_set_by_factory_but_db_nullable():
    # Every creation path sets workshop_role (the factory included); it is only
    # DB-nullable to bootstrap before the D0-3 "undefined" seed exists.
    user = UserFactory()
    assert user.workshop_role is not None
    assert User._meta.get_field("workshop_role").null is True


def test_phone_defaults_to_empty_string():
    assert UserFactory().phone == ""


def test_clearances_m2m_assignable():
    ops = [OperationTypeFactory(), OperationTypeFactory()]
    user = UserFactory(clearances=ops)
    assert set(user.clearances.all()) == set(ops)


def test_factory_produces_saveable_user_with_dob():
    user = UserFactory()
    assert user.pk is not None
    assert user.date_of_birth == datetime.date(1990, 1, 1)
