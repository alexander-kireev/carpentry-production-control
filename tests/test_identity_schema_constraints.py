from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from workshops.models import OperationType, Workshop, WorkshopRole

pytestmark = pytest.mark.django_db


def _workshop(name="Workshop"):
    return Workshop.objects.create(
        name=name,
        address="Address",
        email=f"{name.lower()}@example.com",
        timezone="Europe/London",
    )


def _user(**overrides):
    User = get_user_model()
    values = {
        "password": "!",
        "first_name": "Test",
        "last_name": "User",
        "date_of_birth": date(1990, 1, 1),
        "email": "test@example.com",
        "account_role": "admin",
        "status": "active",
        "onboarding_state": "registered_no_workshop",
    }
    values.update(overrides)
    return User.objects.create(**values)


def test_rejects_invalid_workshop_state_and_lifecycle_edges():
    workshop = _workshop()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Workshop.objects.filter(pk=workshop.pk).update(status="operational")
    workshop.refresh_from_db()
    assert workshop.status == "manager_required"

    Workshop.objects.filter(pk=workshop.pk).update(status="manager_activation_pending")
    Workshop.objects.filter(pk=workshop.pk).update(status="operational")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Workshop.objects.filter(pk=workshop.pk).update(status="manager_required")


def test_exact_unattached_shape_and_half_attachment_are_database_enforced():
    _user()
    workshop = _workshop()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _user(email="half@example.com", workshop=workshop)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _user(
                email="manager-null@example.com",
                account_role="manager",
                status="active",
            )


def test_attachment_is_one_way_and_account_role_is_immutable():
    user = _user()
    workshop = _workshop()
    admin_role = WorkshopRole.objects.get(machine_key="admin")
    user.workshop = workshop
    user.workshop_role = admin_role
    user.onboarding_state = None
    user.save()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            get_user_model().objects.filter(pk=user.pk).update(
                workshop=None,
                workshop_role=None,
                onboarding_state="registered_no_workshop",
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            get_user_model().objects.filter(pk=user.pk).update(account_role="operator")
    user.refresh_from_db()
    assert user.workshop_id == workshop.id
    assert user.account_role == "admin"


def test_role_assignment_is_field_specific_and_tenant_scoped():
    first = _workshop("First")
    second = _workshop("Second")
    first_role = WorkshopRole.objects.create(workshop=first, name="Maker")
    undefined = WorkshopRole.objects.get(machine_key="undefined")
    admin = WorkshopRole.objects.get(machine_key="admin")

    _user(
        email="operator@example.com",
        account_role="operator",
        status="active",
        onboarding_state=None,
        workshop=first,
        workshop_role=first_role,
    )
    _user(
        email="manager@example.com",
        account_role="manager",
        status="pending",
        onboarding_state=None,
        workshop=second,
        workshop_role=undefined,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _user(
                email="cross@example.com",
                account_role="operator",
                status="active",
                onboarding_state=None,
                workshop=second,
                workshop_role=first_role,
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _user(
                email="bad-admin-role@example.com",
                account_role="operator",
                status="active",
                onboarding_state=None,
                workshop=second,
                workshop_role=admin,
            )


def test_permanent_anchor_indexes_and_delete_guard():
    workshop = _workshop()
    undefined = WorkshopRole.objects.get(machine_key="undefined")
    first = _user(
        email="manager1@example.com",
        account_role="manager",
        status="pending",
        onboarding_state=None,
        workshop=workshop,
        workshop_role=undefined,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _user(
                email="manager2@example.com",
                account_role="manager",
                status="pending",
                onboarding_state=None,
                workshop=workshop,
                workshop_role=undefined,
            )
    first.delete()
    assert not get_user_model().objects.filter(pk=first.pk).exists()

    admin = _user(email="undeletable@example.com")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            admin.delete()


def test_protected_rows_reject_mutation_and_deletion():
    role = WorkshopRole.objects.get(machine_key="admin")
    operation_type = OperationType.objects.get(machine_key="other")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkshopRole.objects.filter(pk=role.pk).update(name="Changed")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            role.delete()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OperationType.objects.filter(pk=operation_type.pk).update(
                requires_clearance=True
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            operation_type.delete()


def test_protected_pair_shape_and_permanent_identity_are_enforced():
    workshop = _workshop()
    protected = OperationType.objects.create(
        workshop=workshop,
        name="Build Planning",
        machine_key="build_planning",
        is_production=False,
        requires_clearance=True,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OperationType.objects.create(
                workshop=workshop,
                name="Station Maintenance",
                machine_key="build_planning",
                is_production=False,
                requires_clearance=True,
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OperationType.objects.filter(pk=protected.pk).update(name="Changed")


def test_failed_statement_rolls_back_without_partial_mutation():
    user = _user()
    workshop = _workshop()
    admin_role = WorkshopRole.objects.get(machine_key="admin")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            get_user_model().objects.filter(pk=user.pk).update(first_name="Changed")
            get_user_model().objects.filter(pk=user.pk).update(
                workshop=workshop,
                workshop_role=admin_role,
            )
    user.refresh_from_db()
    assert user.first_name == "Test"
    assert user.workshop_id is None


@pytest.mark.django_db(transaction=True)
def test_invitation_scope_shape_uniqueness_and_transition_guards():
    from identity.commands import invite_permanent_manager
    from identity.models import (
        EmailDeliveryIntent,
        ManagerInvitationCommandReceipt,
        UserInvitation,
    )
    from tests.test_manager_invitation import attached_admin, payload

    admin, _, _ = attached_admin(email="constraint-admin@example.test")
    invite_permanent_manager(
        actor_id=admin.id, data=payload(), idempotency_key="constraints"
    )
    invitation = UserInvitation.objects.get()
    intent = EmailDeliveryIntent.objects.get()
    receipt = ManagerInvitationCommandReceipt.objects.get()
    other = _workshop("OtherConstraint")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            UserInvitation.objects.filter(pk=invitation.pk).update(token_salt=b"")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            UserInvitation.objects.filter(pk=invitation.pk).update(workshop=other)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            EmailDeliveryIntent.objects.create(
                invitation=invitation,
                recipient_email="duplicate@example.test",
                invitation_generation=1,
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            EmailDeliveryIntent.objects.filter(pk=intent.pk).update(
                status="pending", attempt_count=0, last_attempted_at=None
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ManagerInvitationCommandReceipt.objects.filter(pk=receipt.pk).delete()


def test_concurrent_case_variant_email_has_one_committed_winner():
    from concurrent.futures import ThreadPoolExecutor

    import psycopg
    from django.conf import settings

    undefined_id = WorkshopRole.objects.get(machine_key="undefined").pk
    database = settings.DATABASES["default"]
    connect = {
        "dbname": database["NAME"],
        "user": database["USER"],
        "password": database["PASSWORD"],
        "host": database["HOST"],
        "port": database["PORT"],
    }
    with psycopg.connect(**connect) as setup:
        workshop_ids = []
        for index in (1, 2):
            workshop_ids.append(
                setup.execute(
                    """
                    INSERT INTO workshop
                    (name,address,email,timezone,status,version,created_at,
                     station_code_counter,customer_code_counter,order_code_counter,
                     build_code_counter)
                    VALUES (%s,'Address',%s,'Europe/London','manager_required',1,now(),0,0,0,0)
                    RETURNING id
                    """,
                    (f"Concurrent {index}", f"concurrent{index}@example.com"),
                ).fetchone()[0]
            )

    def insert(email, workshop_id):
        try:
            with psycopg.connect(**connect) as session:
                session.execute(
                    """
                    INSERT INTO user_account
                    (password,last_login,first_name,last_name,date_of_birth,email,
                     account_role,onboarding_state,status,date_joined,version,
                     workshop_id,workshop_role_id)
                    VALUES ('!',NULL,'Case','Race','1990-01-01',%s,
                            'manager',NULL,'pending',now(),1,%s,%s)
                    """,
                    (email, workshop_id, undefined_id),
                )
            return "committed"
        except psycopg.errors.UniqueViolation:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: insert(*args),
                [
                    ("race@example.com", workshop_ids[0]),
                    ("RACE@example.com", workshop_ids[1]),
                ],
            )
        )
    assert sorted(results) == ["committed", "rejected"]

    with psycopg.connect(**connect) as cleanup:
        cleanup.execute(
            "DELETE FROM user_account WHERE lower(email) = 'race@example.com'"
        )
        cleanup.execute("DELETE FROM workshop WHERE id = ANY(%s)", (workshop_ids,))


def test_concurrent_permanent_manager_anchor_has_one_committed_winner():
    from concurrent.futures import ThreadPoolExecutor

    import psycopg
    from django.conf import settings

    undefined_id = WorkshopRole.objects.get(machine_key="undefined").pk
    database = settings.DATABASES["default"]
    connect = {
        "dbname": database["NAME"],
        "user": database["USER"],
        "password": database["PASSWORD"],
        "host": database["HOST"],
        "port": database["PORT"],
    }
    with psycopg.connect(**connect) as setup:
        workshop_id = setup.execute(
            """
            INSERT INTO workshop
            (name,address,email,timezone,status,version,created_at,
             station_code_counter,customer_code_counter,order_code_counter,
             build_code_counter)
            VALUES ('Anchor race','Address','anchor-race@example.com','Europe/London',
                    'manager_required',1,now(),0,0,0,0)
            RETURNING id
            """
        ).fetchone()[0]

    def insert(email):
        try:
            with psycopg.connect(**connect) as session:
                session.execute(
                    """
                    INSERT INTO user_account
                    (password,last_login,first_name,last_name,date_of_birth,email,
                     account_role,onboarding_state,status,date_joined,version,
                     workshop_id,workshop_role_id)
                    VALUES ('!',NULL,'Anchor','Race','1990-01-01',%s,
                            'manager',NULL,'pending',now(),1,%s,%s)
                    """,
                    (email, workshop_id, undefined_id),
                )
            return "committed"
        except psycopg.errors.UniqueViolation:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(insert, ("anchor1@example.com", "anchor2@example.com")))
    assert sorted(results) == ["committed", "rejected"]

    with psycopg.connect(**connect) as cleanup:
        cleanup.execute(
            "DELETE FROM user_account WHERE workshop_id = %s", (workshop_id,)
        )
        cleanup.execute("DELETE FROM workshop WHERE id = %s", (workshop_id,))


@pytest.mark.django_db(transaction=True)
def test_registration_receipt_is_immutable_and_restricts_user_delete():
    from datetime import date

    from django.db import DatabaseError, connection, transaction

    from identity.models import RegistrationCommandReceipt, User

    user = User.objects.create_user(
        email="receipt@example.test",
        password="valid-password-483!",
        first_name="Receipt",
        last_name="Owner",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )
    receipt = RegistrationCommandReceipt.objects.create(
        idempotency_key="receipt-key",
        fingerprint_version=1,
        payload_fingerprint=b"f" * 32,
        result_user=user,
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        RegistrationCommandReceipt.objects.filter(pk=receipt.pk).update(
            fingerprint_version=2
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_account WHERE id=%s", [user.pk])


@pytest.mark.django_db
def test_sb02_physical_types_constraints_and_index():
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='activation_code_attempt_bucket' ORDER BY ordinal_position"
        )
        assert cursor.fetchall() == [
            ("id", "bigint"),
            ("hmac_key_version", "smallint"),
            ("client_ip_hmac", "bytea"),
            ("window_started_at", "timestamp with time zone"),
            ("failed_attempt_count", "smallint"),
            ("updated_at", "timestamp with time zone"),
        ]
        cursor.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname='idx_124_activation_window'"
        )
        assert cursor.fetchone() == (1,)


@pytest.mark.django_db(transaction=True)
def test_workshop_creation_receipt_physical_contract_is_immutable():
    from datetime import date

    from django.db import DatabaseError, transaction

    from identity.commands import create_workshop
    from identity.models import User, WorkshopCreationCommandReceipt
    from workshops.models import OperationType, WorkshopRole

    WorkshopRole.objects.get_or_create(
        machine_key="undefined", defaults={"name": "undefined", "status": "active"}
    )
    WorkshopRole.objects.get_or_create(
        machine_key="admin", defaults={"name": "Admin", "status": "active"}
    )
    OperationType.objects.get_or_create(
        machine_key="other",
        defaults={
            "name": "Other",
            "is_production": True,
            "requires_clearance": False,
            "status": "active",
        },
    )

    user = User.objects.create_user(
        email="schema-receipt@example.test",
        password="Valid-password-483!",
        first_name="Schema",
        last_name="Receipt",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )
    data = {
        "submission_nonce": "schema",
        "expected_user_version": 1,
        "name": "Schema Workshop",
        "address": "1 Schema Lane",
        "contact_email": "schema-workshop@example.test",
        "timezone": "Europe/London",
    }
    assert create_workshop(
        actor_id=user.id, data=data, idempotency_key="schema"
    ).succeeded
    receipt = WorkshopCreationCommandReceipt.objects.get()
    with pytest.raises(DatabaseError), transaction.atomic():
        WorkshopCreationCommandReceipt.objects.filter(pk=receipt.pk).update(
            fingerprint_version=2
        )


@pytest.mark.django_db
def test_workshop_creation_receipt_types_constraints_and_trigger_are_exact():
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name,data_type,column_default,is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name='workshop_creation_command_receipt' "
            "ORDER BY ordinal_position"
        )
        assert cursor.fetchall() == [
            ("id", "bigint", None, "NO"),
            ("idempotency_key", "text", None, "NO"),
            ("fingerprint_version", "smallint", None, "NO"),
            ("payload_fingerprint", "bytea", None, "NO"),
            ("created_at", "timestamp with time zone", "now()", "NO"),
            ("actor_user_id", "bigint", None, "NO"),
            ("result_workshop_id", "bigint", None, "NO"),
        ]
        cursor.execute(
            "SELECT conname,contype,confdeltype FROM pg_constraint "
            "WHERE conrelid='workshop_creation_command_receipt'::regclass"
        )
        constraints = set(cursor.fetchall())
        assert {
            ("cst_672_workshop_fingerprint_version_positive", "c", " "),
            ("cst_673_workshop_receipt_actor_fk", "f", "r"),
            ("cst_673_workshop_receipt_result_fk", "f", "r"),
            ("cst_674_workshop_receipt_actor_uniq", "u", " "),
            ("cst_675_workshop_receipt_result_uniq", "u", " "),
        } <= constraints
        cursor.execute(
            "SELECT tgname FROM pg_trigger "
            "WHERE tgrelid='workshop_creation_command_receipt'::regclass "
            "AND NOT tgisinternal"
        )
        assert cursor.fetchall() == [("cst_672_workshop_creation_receipt_immutable",)]
