"""Custom user model.

F1 declared the *minimal* user: an email-login identity plus ``account_role``,
fixed there because ``AUTH_USER_MODEL`` and those attributes lock at the first
migration. D0-2 completes the domain user on that foundation, adding the
``workshop`` / ``workshop_role`` relations, ``status``, ``phone``,
``date_of_birth``, and the ``clearances`` M2M.
"""

import datetime

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
        # Supply the domain fields `createsuperuser` doesn't collect so the CLI
        # yields a usable debug account. Not the real admin path — A1
        # self-registration captures a real DOB — so these are placeholders.
        extra_fields.setdefault("date_of_birth", datetime.date(2000, 1, 1))
        extra_fields.setdefault("account_role", self.model.AccountRole.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Workshop user identity. Login is by email; ``account_role`` routes the UI."""

    class AccountRole(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        OPERATOR = "operator", "Operator"
        TECHNICIAN = "technician", "Technician"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        ON_LEAVE = "on_leave", "On leave"
        INACTIVE = "inactive", "Inactive"

    # Email replaces username as the login identifier.
    username = None
    email = models.EmailField("email address", unique=True)

    account_role = models.CharField(max_length=20, choices=AccountRole.choices)

    # Workshop membership. Nullable because the admin self-registers before the
    # Workshop exists (planning decision 2); SET_NULL so deleting the Workshop
    # (no MVP flow) returns users to that legitimate pre-setup state rather than
    # cascading the account away — a User is an identity that must outlive its
    # workshop, unlike the library rows that CASCADE off it.
    workshop = models.ForeignKey(
        "catalog.Workshop",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    # Always set in practice (every creation path assigns it, defaulting to the
    # "undefined" sentinel seeded in D0-3); DB-nullable only to bootstrap before
    # that seed exists. PROTECT so a role can't be deleted out from under its
    # users — the domain reassigns them to "undefined" at the app level first
    # (later slice), mirroring Station.category / Material.category.
    workshop_role = models.ForeignKey(
        "catalog.WorkshopRole",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    phone = models.CharField(max_length=32, blank=True, default="")
    date_of_birth = models.DateField()
    # Zero or more OperationType clearances (prefilled from the WorkshopRole
    # template at creation — app-level, a later slice).
    clearances = models.ManyToManyField(
        "catalog.OperationType",
        blank=True,
        related_name="cleared_users",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    def __str__(self) -> str:
        return self.email
