"""Custom user model.

F1 declared the *minimal* user: an email-login identity plus ``account_role``,
fixed there because ``AUTH_USER_MODEL`` and those attributes lock at the first
migration. D0-2 completes the domain user on that foundation, adding the
``workshop`` / ``workshop_role`` relations, ``status``, ``phone``,
``date_of_birth``, and the ``clearances`` M2M.
"""

import datetime

from django.conf import settings
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


class ChangeRequest(models.Model):
    """An admin-approved request to change a protected field (Slice D / D1).

    MVP scope is user-identity fields only: a non-admin user requests a change to
    their own ``first_name`` / ``last_name`` / ``date_of_birth``
    (``target_type='user'``); an admin approves; D3's service auto-applies
    ``proposed_value`` to the target field. The station / library-item target_types
    stay dormant under the import-only library narrowing (D-124) — shipped as
    absent choices, not dead ones. Services, UI, and notifications are D3 / N3;
    this ticket is the model only.

    Lives beside ``User`` because a CR mutates ``User`` identity and is assigned to
    the admin. Carries a workshop-scoped ``REQ-###`` business id, mirroring
    ``catalog.Station`` ``ST-NNN`` exactly (CHG-061).
    """

    class TargetType(models.TextChoices):
        # MVP ships the user target only; the station/library-item targets are
        # dormant (D-124) and deliberately absent rather than dead choices.
        USER = "user", "User"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    class CancelReason(models.TextChoices):
        # Why a cancelled CR was cancelled. The tracking surface (D3) must render
        # "Superseded" distinctly from a deactivation-cancel, but ``status`` is a
        # single ``cancelled`` (KI-022). Set only when status=cancelled; NULL
        # otherwise — an invariant D3's service upholds (no DB check constraint).
        SUPERSEDED = "superseded", "Superseded"
        REQUESTER_DEACTIVATED = "requester_deactivated", "Requester deactivated"

    workshop = models.ForeignKey(
        "catalog.Workshop", on_delete=models.CASCADE, related_name="change_requests"
    )
    # Business id (REQ-NNN), system-assigned on create (see save()). Mirrors
    # Station.code: the integer PK stays the internal id, this is the user-facing
    # reference, unique per-workshop (not globally) via the (workshop, code)
    # constraint — two workshops may each hold REQ-001.
    code = models.CharField(max_length=16, blank=True)
    # MVP: always USER. Kept as a generic pointer for the dormant target_types.
    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    # Generic pointer to the target object's PK. For a user-target CR this equals
    # requested_by_id: user-target CR is always self-submitted (no on-behalf path
    # exists for CR), so the pointer collapses onto the requester in MVP. Kept as a
    # distinct field for forward-compatibility with the dormant target_types.
    # PositiveBigIntegerField to match the BigAutoField PK width it references.
    target_id = models.PositiveBigIntegerField()
    # Field being changed; one of first_name / last_name / date_of_birth in MVP.
    # Plain CharField (not choices) — the valid set is target_type-dependent and is
    # validated by D3's service, keeping this pointer generic.
    target_field = models.CharField(max_length=32)
    # Serialized snapshot at submission (audit/context). blank because an existing
    # identity value can legitimately be empty (e.g. a blank last_name).
    current_value = models.CharField(max_length=255, blank=True)
    # Serialized proposed value; written to target_field on approval by D3.
    proposed_value = models.CharField(max_length=255)
    reason = models.TextField()
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_change_requests",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_change_requests",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    cancel_reason = models.CharField(
        max_length=32, choices=CancelReason.choices, null=True, blank=True
    )
    resolution_note = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # REQ-NNN is unique within a workshop, not globally (mirrors
            # uq_station_workshop_code): two workshops may each hold REQ-001.
            models.UniqueConstraint(
                fields=["workshop", "code"],
                name="uq_changerequest_workshop_code",
            ),
            # DB backstop for the one-pending-CR-per-requester guard: at most one
            # pending CR per requester at a time. Closes the check-then-create race
            # KI-009 had to accept for A1/A2; D3's service keeps the friendly
            # pre-check for the UX message, this constraint is the concurrency floor.
            models.UniqueConstraint(
                fields=["requested_by"],
                condition=models.Q(status="pending"),
                name="uniq_pending_cr_per_requester",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._next_code()
        super().save(*args, **kwargs)

    def _next_code(self) -> str:
        """Next sequential REQ-NNN within this CR's own workshop.

        Mirrors ``Station._next_code`` (CHG-061): the sequence is per-workshop —
        every workshop's first CR is REQ-001 and the counter never reflects other
        workshops' CRs (a global counter would leak another workshop's size into a
        user-facing id, breaking D-126 isolation). CRs are submitted one at a time
        via a form, so a simple max+1 within the workshop is sufficient; this is not
        concurrency-hardened, and the (workshop, code) unique constraint is the
        backstop. Do not add select_for_update — matches the ST-NNN posture.
        """
        highest = 0
        existing = ChangeRequest.objects.filter(
            workshop=self.workshop, code__startswith="REQ-"
        ).values_list("code", flat=True)
        for code in existing:
            try:
                highest = max(highest, int(code.rsplit("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return f"REQ-{highest + 1:03d}"

    def __str__(self) -> str:
        return f"{self.code} {self.target_field}".strip()
