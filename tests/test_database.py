import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db
def test_database_vendor_is_postgresql():
    assert connection.vendor == "postgresql"


@pytest.mark.django_db
def test_migration_graph_selects_custom_user_before_auth_and_sessions():
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph

    assert ("identity", "0001_initial") in graph.nodes
    assert ("auth", "0001_initial") in graph.nodes
    assert ("sessions", "0001_initial") in graph.nodes
    assert ("workshops", "0003_seed_protected_identities") in graph.nodes
    assert ("events", "0001_event_notification_boundary") in graph.leaf_nodes()


@pytest.mark.django_db
def test_canonical_tables_exist_without_default_auth_user():
    tables = set(connection.introspection.table_names())

    assert {"workshop", "workshop_role", "operation_type", "user_account"} <= tables
    assert "auth_user" not in tables


@pytest.mark.django_db
def test_sb02_tables_exist():
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename IN ('registration_command_receipt', "
            "'activation_code_attempt_bucket') ORDER BY tablename"
        )
        assert [row[0] for row in cursor.fetchall()] == [
            "activation_code_attempt_bucket",
            "registration_command_receipt",
        ]


@pytest.mark.django_db
def test_sb03_receipt_table_exists():
    assert "workshop_creation_command_receipt" in connection.introspection.table_names()
    assert "user_invitation" in connection.introspection.table_names()
    assert "email_delivery_intent" in connection.introspection.table_names()
    assert (
        "manager_invitation_command_receipt" in connection.introspection.table_names()
    )


@pytest.mark.django_db
def test_sb05_event_boundary_tables_exist():
    assert {"event", "event_notification_intent", "notification"} <= set(
        connection.introspection.table_names()
    )
