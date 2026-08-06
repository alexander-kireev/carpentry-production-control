from dataclasses import dataclass, field
from enum import StrEnum


class ResultCode(StrEnum):
    SUCCESS = "success"
    REGISTRATION_UNAVAILABLE = "registration_unavailable"
    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_FAILED = "authentication_failed"
    SESSION_FAILED = "session_failed"


class Destination(StrEnum):
    LOGIN = "/login"
    CREATE_WORKSHOP = "/onboarding/workshop"


@dataclass(frozen=True)
class CommandResult:
    code: ResultCode
    user: object | None = None
    errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def succeeded(self):
        return self.code == ResultCode.SUCCESS


@dataclass(frozen=True)
class DestinationResult:
    destination: Destination
    supported: bool
