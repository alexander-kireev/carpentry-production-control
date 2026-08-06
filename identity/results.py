from dataclasses import dataclass, field
from enum import StrEnum


class ResultCode(StrEnum):
    SUCCESS = "success"
    REGISTRATION_UNAVAILABLE = "registration_unavailable"
    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_FAILED = "authentication_failed"
    SESSION_FAILED = "session_failed"
    WORKSHOP_UNAVAILABLE = "workshop_unavailable"
    STALE = "stale"
    REPLAY = "replay"
    ALREADY_ADVANCED = "already_advanced"
    INVITATION_UNAVAILABLE = "invitation_unavailable"
    DELIVERY_PENDING = "delivery_pending"
    DELIVERY_SENT = "delivery_sent"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_NOOP = "delivery_noop"
    TIMEZONE_UNAVAILABLE = "timezone_unavailable"


class Destination(StrEnum):
    LOGIN = "/login"
    CREATE_WORKSHOP = "/onboarding/workshop"
    INVITE_MANAGER = "/onboarding/manager"
    SETUP_COCKPIT = "/onboarding"
    HOLDING = "/onboarding/holding"
    DASHBOARD = "/dashboard"


@dataclass(frozen=True)
class CommandResult:
    code: ResultCode
    user: object | None = None
    errors: dict[str, list[str]] = field(default_factory=dict)
    workshop: object | None = None
    candidate: object | None = None
    invitation: object | None = None
    delivery_intent: object | None = None
    events: tuple = ()

    @property
    def succeeded(self):
        return self.code == ResultCode.SUCCESS


@dataclass(frozen=True)
class DestinationResult:
    destination: Destination
    supported: bool
    role_home: str | None = None
    user: object | None = None


@dataclass(frozen=True)
class InvitationEnvelope:
    available: bool
    selector: int | None = None
    generation: int | None = None
    candidate_name: str | None = None
    candidate_email: str | None = None
    workshop_name: str | None = None
