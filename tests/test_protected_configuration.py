import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from workshops.models import OperationType, WorkshopRole
from workshops.protected_configuration import (
    ProtectedConfigurationError,
    resolve_protected_configuration,
)

pytestmark = pytest.mark.django_db


def test_resolver_and_command_accept_exact_configuration(capsys):
    protected = resolve_protected_configuration()
    assert protected.undefined_role.machine_key == "undefined"
    assert protected.admin_role.machine_key == "admin"
    assert protected.other_operation_type.machine_key == "other"

    call_command("verify_protected_configuration")
    assert "verification succeeded" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("table", "mutation"),
    [
        ("workshop_role", "DELETE FROM workshop_role WHERE machine_key = 'undefined'"),
        (
            "workshop_role",
            "UPDATE workshop_role SET name = 'renamed' WHERE machine_key = 'admin'",
        ),
        (
            "operation_type",
            "UPDATE operation_type SET is_production = false WHERE machine_key = 'other'",
        ),
    ],
)
def test_resolver_fails_closed_for_missing_renamed_or_corrupt_rows(table, mutation):
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        cursor.execute(mutation)

    before = (
        WorkshopRole.objects.count(),
        OperationType.objects.count(),
    )
    with pytest.raises(ProtectedConfigurationError):
        resolve_protected_configuration()
    with pytest.raises(CommandError, match="verification failed"):
        call_command("verify_protected_configuration")
    after = (
        WorkshopRole.objects.count(),
        OperationType.objects.count(),
    )
    assert after == before


def test_resolver_rejects_ambiguous_global_configuration():
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE workshop_role DISABLE TRIGGER cst_012_013_workshop_role_guard"
        )
        cursor.execute(
            """
            INSERT INTO workshop_role (name, machine_key, status, version)
            VALUES ('Unexpected', 'unexpected', 'active', 1)
            """
        )
    with pytest.raises(ProtectedConfigurationError):
        resolve_protected_configuration()


def test_resolver_rejects_retired_protected_configuration():
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE workshop_role DISABLE TRIGGER cst_012_013_workshop_role_guard"
        )
        cursor.execute(
            "ALTER TABLE workshop_role DROP CONSTRAINT cst_012_workshop_role_sentinel_active"
        )
        cursor.execute(
            "UPDATE workshop_role SET status = 'retired' WHERE machine_key = 'admin'"
        )
    with pytest.raises(ProtectedConfigurationError):
        resolve_protected_configuration()


def test_verifier_accepts_any_positive_version_and_rejects_zero_without_repair():
    WorkshopRole.objects.filter(machine_key__in=("undefined", "admin")).update(
        version=2
    )
    OperationType.objects.filter(machine_key="other").update(version=2)
    resolve_protected_configuration()

    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE workshop_role DROP CONSTRAINT cst_008_workshop_role_version_positive"
        )
        cursor.execute(
            "ALTER TABLE operation_type DROP CONSTRAINT cst_038_operation_type_version_positive"
        )
        cursor.execute(
            "UPDATE workshop_role SET version = 0 WHERE machine_key IN ('undefined', 'admin')"
        )
        cursor.execute(
            "UPDATE operation_type SET version = 0 WHERE machine_key = 'other'"
        )

    with pytest.raises(CommandError) as error:
        call_command("verify_protected_configuration")
    assert str(error.value) == "Protected configuration verification failed"
    assert set(
        WorkshopRole.objects.filter(machine_key__in=("undefined", "admin")).values_list(
            "version", flat=True
        )
    ) == {0}
    assert OperationType.objects.get(machine_key="other").version == 0
