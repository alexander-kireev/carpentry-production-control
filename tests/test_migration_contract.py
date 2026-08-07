import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from workshops.models import OperationType, WorkshopRole

pytestmark = pytest.mark.django_db


def test_migration_order_and_custom_user_first_migration():
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes(), clean_start=True)
    nodes = [migration.app_label + "." + migration.name for migration, _ in plan]

    assert "workshops.0001_initial" in nodes
    assert "identity.0001_initial" in nodes
    assert nodes.index("workshops.0001_initial") < nodes.index("identity.0001_initial")
    assert nodes.index("identity.0001_initial") < nodes.index("auth.0001_initial")
    assert nodes.index("identity.0001_initial") < nodes.index("sessions.0001_initial")
    assert nodes.index("identity.0001_initial") < nodes.index(
        "identity.0002_database_guards"
    )
    assert nodes.index("identity.0002_database_guards") < nodes.index(
        "workshops.0003_seed_protected_identities"
    )


def test_seed_is_exact_and_idempotent():
    role_shape = set(
        WorkshopRole.objects.filter(workshop__isnull=True).values_list(
            "machine_key", "name", "status"
        )
    )
    type_shape = set(
        OperationType.objects.filter(workshop__isnull=True).values_list(
            "machine_key", "name", "status", "is_production", "requires_clearance"
        )
    )
    assert role_shape == {
        ("undefined", "undefined", "active"),
        ("admin", "Admin", "active"),
    }
    assert type_shape == {("other", "Other", "active", True, False)}

    migration = importlib.import_module(
        "workshops.migrations.0003_seed_protected_identities"
    )
    migration.seed_protected_identities(apps, SimpleNamespace(connection=connection))
    assert WorkshopRole.objects.filter(workshop__isnull=True).count() == 2
    assert OperationType.objects.filter(workshop__isnull=True).count() == 1


def test_schema_catalogue_contains_named_constraints_indexes_and_triggers():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid IN (
                'workshop'::regclass,
                'workshop_role'::regclass,
                'operation_type'::regclass,
                'user_account'::regclass
            )
            """
        )
        constraints = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename IN ('workshop', 'workshop_role', 'operation_type', 'user_account')
            """
        )
        indexes = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT tgname FROM pg_trigger
            WHERE tgrelid IN (
                'workshop'::regclass,
                'workshop_role'::regclass,
                'operation_type'::regclass,
                'user_account'::regclass
            ) AND NOT tgisinternal
            """
        )
        triggers = {row[0] for row in cursor.fetchall()}

    assert {
        "cst_001_workshop_status",
        "cst_003_workshop_version_positive",
        "cst_007_workshop_role_status",
        "cst_008_workshop_role_version_positive",
        "cst_015_user_names_nonempty",
        "cst_017_user_account_role",
        "cst_018_user_status",
        "cst_019_user_version_positive",
        "cst_023_user_inactive_operator",
        "cst_038_operation_type_version_positive",
        "cst_047_operation_type_status",
        "cst_049_operation_type_name_nonempty",
        "cst_663_user_attachment_shape",
    } <= constraints
    assert {
        "cst_016_user_email_lower_uniq",
        "cst_024_user_active_admin_uniq",
        "cst_025_user_manager_uniq",
        "cst_041_operation_type_name_uniq",
        "cst_043_global_operation_type_key_uniq",
    } <= indexes
    assert {
        "cst_002_workshop_lifecycle",
        "cst_046_operation_type_guard",
        "cst_014_022_026_664_user_write_guard",
        "cst_020_user_delete_guard",
    } <= triggers


def test_canonical_columns_and_foreign_keys_are_exact():
    expected_columns = {
        "workshop": {
            "id",
            "name",
            "address",
            "email",
            "timezone",
            "status",
            "version",
            "created_at",
            "timezone_correction_idempotency_key",
            "station_code_counter",
            "customer_code_counter",
            "order_code_counter",
            "build_code_counter",
        },
        "workshop_role": {
            "id",
            "workshop_id",
            "name",
            "description",
            "machine_key",
            "status",
            "version",
        },
        "operation_type": {
            "id",
            "workshop_id",
            "name",
            "description",
            "is_production",
            "requires_clearance",
            "machine_key",
            "first_referenced_at",
            "status",
            "version",
        },
        "user_account": {
            "id",
            "workshop_id",
            "first_name",
            "last_name",
            "date_of_birth",
            "avatar_path",
            "email",
            "password",
            "phone",
            "account_role",
            "workshop_role_id",
            "onboarding_state",
            "status",
            "date_joined",
            "last_login",
            "version",
        },
    }
    with connection.cursor() as cursor:
        for table, expected in expected_columns.items():
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                (table,),
            )
            assert {row[0] for row in cursor.fetchall()} == expected
        cursor.execute(
            """
            SELECT conrelid::regclass::text, confrelid::regclass::text, confdeltype
            FROM pg_constraint
            WHERE contype = 'f' AND conrelid IN (
                'workshop_role'::regclass,
                'operation_type'::regclass,
                'user_account'::regclass
            )
            """
        )
        foreign_keys = set(cursor.fetchall())
    assert ("workshop_role", "workshop", "r") in foreign_keys
    assert ("operation_type", "workshop", "r") in foreign_keys
    assert ("user_account", "workshop", "r") in foreign_keys
    assert ("user_account", "workshop_role", "r") in foreign_keys


def test_canonical_physical_types_and_defaults_are_exact():
    expected = {
        ("workshop", "status"): ("text", "'manager_required'::text"),
        ("workshop", "version"): ("integer", "1"),
        ("workshop", "created_at"): ("timestamp with time zone", "now()"),
        ("workshop", "station_code_counter"): ("integer", "0"),
        ("workshop", "customer_code_counter"): ("integer", "0"),
        ("workshop", "order_code_counter"): ("integer", "0"),
        ("workshop", "build_code_counter"): ("integer", "0"),
        ("workshop_role", "status"): ("text", "'active'::text"),
        ("workshop_role", "version"): ("integer", "1"),
        ("operation_type", "requires_clearance"): ("boolean", "true"),
        ("operation_type", "status"): ("text", "'active'::text"),
        ("operation_type", "version"): ("integer", "1"),
        ("user_account", "password"): ("text", None),
        ("user_account", "date_joined"): ("timestamp with time zone", "now()"),
        ("user_account", "version"): ("integer", "1"),
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name, column_name, data_type, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN (
                  'workshop', 'workshop_role', 'operation_type', 'user_account'
              )
            """
        )
        actual = {
            (table, column): (data_type, default)
            for table, column, data_type, default in cursor.fetchall()
            if (table, column) in expected
        }
    assert actual == expected


def test_direct_inserts_receive_canonical_database_defaults():
    long_password_hash = "x" * 256
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workshop (name, address, email, timezone)
            VALUES ('Default Workshop', 'Address', 'defaults@example.com', 'UTC')
            RETURNING id, status, version, created_at,
                      station_code_counter, customer_code_counter,
                      order_code_counter, build_code_counter
            """
        )
        (
            workshop_id,
            workshop_status,
            workshop_version,
            created_at,
            station_counter,
            customer_counter,
            order_counter,
            build_counter,
        ) = cursor.fetchone()
        assert (
            workshop_status,
            workshop_version,
            station_counter,
            customer_counter,
            order_counter,
            build_counter,
        ) == ("manager_required", 1, 0, 0, 0, 0)
        assert created_at is not None

        cursor.execute(
            """
            INSERT INTO workshop_role (workshop_id, name)
            VALUES (%s, 'Default Role')
            RETURNING id, status, version
            """,
            (workshop_id,),
        )
        role_id, role_status, role_version = cursor.fetchone()
        assert (role_status, role_version) == ("active", 1)

        cursor.execute(
            """
            INSERT INTO operation_type (workshop_id, name, is_production)
            VALUES (%s, 'Default Operation', true)
            RETURNING requires_clearance, status, version
            """,
            (workshop_id,),
        )
        assert cursor.fetchone() == (True, "active", 1)

        cursor.execute(
            """
            INSERT INTO user_account (
                password, first_name, last_name, date_of_birth, email,
                account_role, workshop_id, workshop_role_id, status
            )
            VALUES (%s, 'Default', 'User', '1990-01-01',
                    'direct-default@example.com', 'operator', %s, %s, 'active')
            RETURNING length(password), date_joined, version
            """,
            (long_password_hash, workshop_id, role_id),
        )
        password_length, date_joined, user_version = cursor.fetchone()
        assert password_length == 256
        assert date_joined is not None
        assert user_version == 1


def test_current_slice_library_and_event_subject_tables_exist():
    tables = set(connection.introspection.table_names())
    assert "user_account" in tables
    assert "auth_user" not in tables
    assert {
        "material_category",
        "unit_type",
        "shift_definition",
        "workshop_role_default_clearance",
        "configuration_command_receipt",
        "event_subject",
    } <= tables
    assert "user_invitation" in tables
    assert "email_delivery_intent" in tables
    assert "manager_invitation_command_receipt" in tables
    assert {
        "event",
        "event_subject",
        "event_notification_intent",
        "notification",
    } <= tables


def test_sb02_migration_is_additive_and_reversible_sql():
    from importlib import import_module

    migration = import_module("identity.migrations.0003_registration_access")
    assert (
        "CREATE TRIGGER cst_669_registration_receipt_immutable" in migration.FORWARD_SQL
    )
    assert (
        "DROP FUNCTION IF EXISTS public.sb02_registration_receipt_immutable"
        in migration.REVERSE_SQL
    )


def test_sb03_migration_is_additive_and_reversible_sql():
    migration = importlib.import_module(
        "identity.migrations.0004_workshop_establishment"
    )
    assert (
        "CREATE TRIGGER cst_672_workshop_creation_receipt_immutable"
        in migration.FORWARD_SQL
    )
    assert (
        "DROP FUNCTION IF EXISTS public.sb03_workshop_creation_receipt_immutable"
        in migration.REVERSE_SQL
    )


def test_sb04_migration_is_additive_and_reversible_sql():
    migration = importlib.import_module(
        "identity.migrations.0005_manager_invitation_delivery"
    )
    assert "cst_665_user_invitation_candidate_scope_fk" in migration.FORWARD_SQL
    assert "cst_668_email_delivery_transition_guard" in migration.FORWARD_SQL
    assert (
        "DROP FUNCTION IF EXISTS public.sb04_manager_receipt_guard"
        in migration.REVERSE_SQL
    )


def test_sb05_migration_has_sequence_and_event_immutability_guard():
    migration = importlib.import_module(
        "events.migrations.0001_event_notification_boundary"
    )
    sql = "\n".join(
        operation.sql
        for operation in migration.Migration.operations
        if hasattr(operation, "sql")
    )
    assert "CREATE SEQUENCE event_sequence_number_seq" in sql
    assert "CREATE TRIGGER trg_event_immutable" in sql
