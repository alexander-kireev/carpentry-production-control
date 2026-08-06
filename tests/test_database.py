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
    assert ("workshops", "0003_seed_protected_identities") in graph.leaf_nodes()


@pytest.mark.django_db
def test_canonical_tables_exist_without_default_auth_user():
    tables = set(connection.introspection.table_names())

    assert {"workshop", "workshop_role", "operation_type", "user_account"} <= tables
    assert "auth_user" not in tables
