from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from workshops.models import Workshop, WorkshopRole

pytestmark = pytest.mark.django_db


def test_custom_user_mapping_and_auth_contract():
    User = get_user_model()

    assert User._meta.db_table == "user_account"
    assert User.USERNAME_FIELD == "email"
    assert User.EMAIL_FIELD == "email"
    assert not hasattr(User, "username")
    assert not hasattr(User, "is_superuser")


def test_manager_normalizes_email_and_hashes_password():
    User = get_user_model()
    user = User.objects.create_user(
        email="  ADMIN@Example.COM ",
        password="correct horse battery staple",
        first_name="Ada",
        last_name="Admin",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )

    assert user.email == "admin@example.com"
    assert user.check_password("correct horse battery staple")
    assert user.is_active


def test_model_bypass_still_enforces_case_insensitive_email_uniqueness():
    User = get_user_model()
    fields = {
        "password": "!",
        "first_name": "First",
        "last_name": "Admin",
        "date_of_birth": date(1990, 1, 1),
        "account_role": "admin",
        "status": "active",
        "onboarding_state": "registered_no_workshop",
    }
    User.objects.bulk_create([User(email="person@example.com", **fields)])

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.bulk_create([User(email="PERSON@example.com", **fields)])


def test_attached_admin_uses_global_admin_role():
    User = get_user_model()
    workshop = Workshop.objects.create(
        name="Workshop",
        address="Address",
        email="workshop@example.com",
        timezone="Europe/London",
    )
    admin_role = WorkshopRole.objects.get(machine_key="admin")
    user = User.objects.create(
        password="!",
        first_name="Ada",
        last_name="Admin",
        date_of_birth=date(1990, 1, 1),
        email="attached@example.com",
        account_role="admin",
        workshop=workshop,
        workshop_role=admin_role,
        status="active",
    )

    assert user.workshop_id == workshop.id
    assert user.workshop_role_id == admin_role.id
