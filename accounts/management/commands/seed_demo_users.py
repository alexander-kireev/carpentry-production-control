"""Dev-only ``seed_demo_users`` management command.

Creates a demo Workshop and one **active** login per ``account_role`` (admin /
manager / operator / technician) so the Slice-S shell can be exercised before
Slice B's real invitation flow exists. It is the stopgap named in
``development_strategy.md`` §7 and is **superseded by Slice B** — retire it once
invitations land.

Dev-only and idempotent: it refuses to run with ``DEBUG=False`` and every row it
creates is guarded by an existence check, so re-running never duplicates.

Singleton caveat: creating the demo Workshop satisfies (and thereby bypasses) the
real A1 registration + A2 setup-gate flow. This is a shell-demo tool for a
throwaway dev database, **not** to be combined with a manual A walkthrough on the
same DB — use a fresh database to exercise the real registration/gate flow.
"""

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from catalog.models import Workshop, WorkshopRole
from catalog.seeds import ADMIN_ROLE_NAME, UNDEFINED_NAME

# Obviously-synthetic demo values. Passwords are intentionally trivial and are
# echoed to stdout — acceptable only because the command is DEBUG-gated.
DEMO_DATE_OF_BIRTH = datetime.date(1990, 1, 1)

DEMO_WORKSHOP = {
    "name": "Demo Carpentry Workshop",
    "address": "1 Timber Lane",
    "email": "workshop@demo.local",
}

# One login per account_role. workshop_role: admin -> the seeded "Admin" role,
# everyone else -> the seeded "undefined" sentinel (both from D0-3).
DEMO_USERS = [
    {
        "account_role": User.AccountRole.ADMIN,
        "workshop_role_name": ADMIN_ROLE_NAME,
        "email": "admin@demo.local",
        "password": "demo-admin-pass",
        "first_name": "Demo",
        "last_name": "Admin",
    },
    {
        "account_role": User.AccountRole.MANAGER,
        "workshop_role_name": UNDEFINED_NAME,
        "email": "manager@demo.local",
        "password": "demo-manager-pass",
        "first_name": "Demo",
        "last_name": "Manager",
    },
    {
        "account_role": User.AccountRole.OPERATOR,
        "workshop_role_name": UNDEFINED_NAME,
        "email": "operator@demo.local",
        "password": "demo-operator-pass",
        "first_name": "Demo",
        "last_name": "Operator",
    },
    {
        "account_role": User.AccountRole.TECHNICIAN,
        "workshop_role_name": UNDEFINED_NAME,
        "email": "technician@demo.local",
        "password": "demo-technician-pass",
        "first_name": "Demo",
        "last_name": "Technician",
    },
]


class Command(BaseCommand):
    help = (
        "Dev-only: create a demo Workshop and one active login per role to "
        "exercise the shell. Idempotent; refuses to run with DEBUG=False. "
        "Superseded by Slice B's invitation flow."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo_users is a dev-only tool and refuses to run with "
                "DEBUG=False. It creates logins with known demo passwords; run it "
                "only against a development database."
            )

        with transaction.atomic():
            workshop, workshop_created = self._ensure_workshop()
            roles = self._load_roles()
            created, existing = self._ensure_users(workshop, roles)

        self._report(workshop, workshop_created, created, existing)

    def _ensure_workshop(self):
        """Return the demo Workshop, creating the singleton if none exists."""
        workshop = Workshop.objects.first()
        if workshop is not None:
            return workshop, False
        return Workshop.objects.create(**DEMO_WORKSHOP), True

    def _load_roles(self):
        """Look up the D0-3 seeded, workshop-independent WorkshopRole sentinels."""
        try:
            return {
                ADMIN_ROLE_NAME: WorkshopRole.objects.get(
                    workshop__isnull=True, name=ADMIN_ROLE_NAME
                ),
                UNDEFINED_NAME: WorkshopRole.objects.get(
                    workshop__isnull=True, name=UNDEFINED_NAME
                ),
            }
        except WorkshopRole.DoesNotExist as exc:
            raise CommandError(
                "Seeded WorkshopRole sentinels are missing — run `migrate` so the "
                "D0-3 system seeds exist before seeding demo users."
            ) from exc

    def _ensure_users(self, workshop, roles):
        """Create any missing demo users. Returns (created, existing) email lists."""
        created, existing = [], []
        for spec in DEMO_USERS:
            email = spec["email"]
            if User.objects.filter(email=email).exists():
                existing.append(email)
                continue
            User.objects.create_user(
                email=email,
                password=spec["password"],
                account_role=spec["account_role"],
                workshop=workshop,
                workshop_role=roles[spec["workshop_role_name"]],
                status=User.Status.ACTIVE,
                date_of_birth=DEMO_DATE_OF_BIRTH,
                first_name=spec["first_name"],
                last_name=spec["last_name"],
            )
            created.append(email)
        return created, existing

    def _report(self, workshop, workshop_created, created, existing):
        verb = "created" if workshop_created else "reused"
        self.stdout.write(f"Workshop {verb}: {workshop.name} (id={workshop.pk})")
        self.stdout.write("Demo logins (role / email / password):")
        for spec in DEMO_USERS:
            state = "created" if spec["email"] in created else "exists"
            self.stdout.write(
                f"  {spec['account_role'].value:10} {spec['email']:22} "
                f"{spec['password']:22} [{state}]"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {len(created)} created, {len(existing)} already present."
            )
        )
