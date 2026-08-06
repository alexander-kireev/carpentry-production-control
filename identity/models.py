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
