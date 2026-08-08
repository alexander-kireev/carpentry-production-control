"""Forward dependency seams for Station configuration.

Operation and MaintenanceJob persistence is intentionally absent from the current
application.  These adapters keep the command boundary explicit without inventing
placeholder rows.  Their owning slices must replace the empty implementations
before either source becomes writable.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StationBlocker:
    code: str


def locked_operations_blocking_capability_removal(
    *, station_id: int, operation_type_ids: tuple[int, ...]
) -> tuple[StationBlocker, ...]:
    del station_id, operation_type_ids
    return ()


def locked_operations_blocking_retirement(
    *,
    station_id: int,
) -> tuple[StationBlocker, ...]:
    del station_id
    return ()


def locked_maintenance_jobs_blocking_retirement(
    *,
    station_id: int,
) -> tuple[StationBlocker, ...]:
    del station_id
    return ()
