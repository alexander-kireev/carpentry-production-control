import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db
def test_database_vendor_is_postgresql():
    assert connection.vendor == "postgresql"


@pytest.mark.django_db
def test_migration_graph_is_empty():
    executor = MigrationExecutor(connection)

    assert executor.loader.graph.leaf_nodes() == []
