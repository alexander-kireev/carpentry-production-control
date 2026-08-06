from dataclasses import dataclass

from identity.models import User
from workshops.models import WorkshopRole


@dataclass(frozen=True)
class RecipientPresentation:
    user_id: int
    title: str
    body: str


def resolve_recipients(event):
    if event.event_type in {
        "WORKSHOP_TIMEZONE_CHANGED",
        "USER_INVITATION_ACCEPTED",
    }:
        return []
    if event.event_type != "WORKSHOP_BECAME_OPERATIONAL":
        raise ValueError("Unsupported recipient policy")
    if event.primary_subject_type != "workshop" or not event.primary_subject_id:
        raise ValueError("Invalid Workshop routing context")
    users = list(
        User.objects.select_related("workshop_role").filter(
            workshop_id=event.primary_subject_id,
            account_role=User.AccountRole.ADMIN,
            status=User.Status.ACTIVE,
            onboarding_state__isnull=True,
        )
    )
    exact = [
        user
        for user in users
        if user.workshop_role is not None
        and user.workshop_role.workshop_id is None
        and user.workshop_role.machine_key == "admin"
        and user.workshop_role.name == "Admin"
        and user.workshop_role.status == WorkshopRole.Status.ACTIVE
    ]
    if len(exact) != 1:
        raise ValueError("Permanent administrator routing is unavailable")
    return [
        RecipientPresentation(
            user_id=exact[0].id,
            title="Workshop setup complete",
            body="Your Workshop is operational.",
        )
    ]
