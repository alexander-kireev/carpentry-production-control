from dataclasses import dataclass

from .models import OperationType, WorkshopRole


class ProtectedConfigurationError(RuntimeError):
    """Raised when required protected database identities are not exact."""


@dataclass(frozen=True)
class ProtectedConfiguration:
    undefined_role: WorkshopRole
    admin_role: WorkshopRole
    other_operation_type: OperationType


def resolve_protected_configuration():
    roles = list(
        WorkshopRole.objects.filter(workshop__isnull=True).order_by("machine_key")
    )
    operation_types = list(
        OperationType.objects.filter(workshop__isnull=True).order_by("machine_key")
    )
    expected_roles = {
        ("undefined", "undefined", WorkshopRole.Status.ACTIVE, True),
        ("admin", "Admin", WorkshopRole.Status.ACTIVE, True),
    }
    actual_roles = {
        (row.machine_key, row.name, row.status, row.version > 0) for row in roles
    }
    expected_types = {
        (
            "other",
            "Other",
            OperationType.Status.ACTIVE,
            True,
            False,
            True,
        )
    }
    actual_types = {
        (
            row.machine_key,
            row.name,
            row.status,
            row.is_production,
            row.requires_clearance,
            row.version > 0,
        )
        for row in operation_types
    }
    if actual_roles != expected_roles or actual_types != expected_types:
        raise ProtectedConfigurationError("Protected configuration is invalid")

    by_key = {row.machine_key: row for row in roles}
    return ProtectedConfiguration(
        undefined_role=by_key["undefined"],
        admin_role=by_key["admin"],
        other_operation_type=operation_types[0],
    )
