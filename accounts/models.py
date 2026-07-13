"""Custom user model.

F1 declares the *minimal* user: an email-login identity plus ``account_role``.
Both are fixed here because ``AUTH_USER_MODEL`` and these two attributes are
locked at the first migration. All other User domain fields (workshop, status,
phone, date_of_birth, workshop_role, clearances) are added in D0-1.
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Email-based manager: ``username`` is removed, so users are keyed by email."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Workshop user identity. Login is by email; ``account_role`` routes the UI."""

    class AccountRole(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        OPERATOR = "operator", "Operator"
        TECHNICIAN = "technician", "Technician"

    # Email replaces username as the login identifier.
    username = None
    email = models.EmailField("email address", unique=True)

    account_role = models.CharField(max_length=20, choices=AccountRole.choices)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    def __str__(self) -> str:
        return self.email
