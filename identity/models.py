from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.db.models.expressions import RawSQL
from django.db.models.functions import Lower

from .managers import UserManager


class User(AbstractBaseUser):
    class AccountRole(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        OPERATOR = "operator", "Operator"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    class OnboardingState(models.TextChoices):
        REGISTERED_NO_WORKSHOP = (
            "registered_no_workshop",
            "Registered without workshop",
        )

    password = models.TextField()
    workshop = models.ForeignKey(
        "workshops.Workshop",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="users",
    )
    first_name = models.TextField()
    last_name = models.TextField()
    date_of_birth = models.DateField()
    avatar_path = models.TextField(null=True, blank=True)
    email = models.TextField()
    phone = models.TextField(null=True, blank=True)
    account_role = models.TextField(choices=AccountRole.choices)
    workshop_role = models.ForeignKey(
        "workshops.WorkshopRole",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="users",
    )
    onboarding_state = models.TextField(
        choices=OnboardingState.choices, null=True, blank=True
    )
    status = models.TextField(choices=Status.choices)
    date_joined = models.DateTimeField(
        auto_now_add=True, db_default=RawSQL("now()", ())
    )
    version = models.PositiveIntegerField(default=1, db_default=1)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"

    class Meta:
        db_table = "user_account"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(account_role__in=("admin", "manager", "operator")),
                name="cst_017_user_account_role",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=("pending", "active", "inactive")),
                name="cst_018_user_status",
            ),
            models.CheckConstraint(
                condition=~models.Q(first_name="") & ~models.Q(last_name=""),
                name="cst_015_user_names_nonempty",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="cst_019_user_version_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="inactive")
                | models.Q(account_role="operator"),
                name="cst_023_user_inactive_operator",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        account_role="admin",
                        status="active",
                        onboarding_state="registered_no_workshop",
                        workshop__isnull=True,
                        workshop_role__isnull=True,
                    )
                    | models.Q(
                        onboarding_state__isnull=True,
                        workshop__isnull=False,
                        workshop_role__isnull=False,
                    )
                ),
                name="cst_663_user_attachment_shape",
            ),
            models.UniqueConstraint(
                Lower("email"), name="cst_016_user_email_lower_uniq"
            ),
            models.UniqueConstraint(
                fields=("id", "workshop"), name="cst_021_user_id_workshop_uniq"
            ),
            models.UniqueConstraint(
                fields=("workshop",),
                condition=models.Q(account_role="admin", status="active"),
                name="cst_024_user_active_admin_uniq",
            ),
            models.UniqueConstraint(
                fields=("workshop",),
                condition=models.Q(account_role="manager"),
                name="cst_025_user_manager_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("workshop", "status", "account_role"), name="idx_006_user_list"
            ),
            models.Index(fields=("workshop_role",), name="idx_007_user_role"),
        ]

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def save(self, *args, **kwargs):
        self.email = self.__class__.objects.normalize_email(self.email)
        super().save(*args, **kwargs)


class RegistrationCommandReceipt(models.Model):
    idempotency_key = models.TextField(unique=True)
    fingerprint_version = models.SmallIntegerField()
    payload_fingerprint = models.BinaryField()
    result_user = models.OneToOneField(
        User,
        on_delete=models.RESTRICT,
        related_name="registration_receipt",
        db_constraint=False,
    )
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "registration_command_receipt"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fingerprint_version__gt=0),
                name="cst_669_registration_fingerprint_version_positive",
            )
        ]


class WorkshopCreationCommandReceipt(models.Model):
    idempotency_key = models.TextField(unique=True)
    fingerprint_version = models.SmallIntegerField()
    payload_fingerprint = models.BinaryField()
    actor_user = models.ForeignKey(
        User,
        on_delete=models.RESTRICT,
        related_name="workshop_creation_receipt",
        db_constraint=False,
    )
    result_workshop = models.ForeignKey(
        "workshops.Workshop",
        on_delete=models.RESTRICT,
        related_name="creation_receipt",
        db_constraint=False,
    )
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "workshop_creation_command_receipt"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fingerprint_version__gt=0),
                name="cst_672_workshop_fingerprint_version_positive",
            ),
            models.UniqueConstraint(
                fields=("actor_user",), name="cst_674_workshop_receipt_actor_uniq"
            ),
            models.UniqueConstraint(
                fields=("result_workshop",),
                name="cst_675_workshop_receipt_result_uniq",
            ),
        ]


class ActivationCodeAttemptBucket(models.Model):
    hmac_key_version = models.SmallIntegerField()
    client_ip_hmac = models.BinaryField()
    window_started_at = models.DateTimeField()
    failed_attempt_count = models.SmallIntegerField()
    updated_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    class Meta:
        db_table = "activation_code_attempt_bucket"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(hmac_key_version__gt=0),
                name="cst_679_activation_hmac_version_positive",
            ),
            models.UniqueConstraint(
                fields=("hmac_key_version", "client_ip_hmac"),
                name="cst_680_activation_bucket_identity_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(failed_attempt_count__gte=0)
                & models.Q(failed_attempt_count__lte=5),
                name="cst_681_activation_failure_count_range",
            ),
            models.CheckConstraint(
                condition=models.Q(updated_at__gte=models.F("window_started_at")),
                name="cst_681_activation_timestamps_ordered",
            ),
        ]
        indexes = [
            models.Index(
                fields=("window_started_at",), name="idx_124_activation_window"
            )
        ]
