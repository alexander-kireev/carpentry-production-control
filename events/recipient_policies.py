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
    } or event.event_type.endswith(("_CREATED", "_UPDATED", "_EDITED")):
        return []
    if event.event_type.endswith(("_RETIRED", "_RESTORED")):
        return _resolve_library_manager(event)
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


def _resolve_library_manager(event):
    from workshops.models import (
        MaterialCategory,
        OperationType,
        ShiftDefinition,
        UnitType,
        WorkshopRole,
    )

    models_by_type = {
        "workshop_role": WorkshopRole,
        "operation_type": OperationType,
        "unit_type": UnitType,
        "material_category": MaterialCategory,
        "shift_definition": ShiftDefinition,
    }
    model = models_by_type.get(event.primary_subject_type)
    if model is None or not event.primary_subject_id:
        raise ValueError("Invalid library routing context")
    try:
        source = model.objects.get(pk=event.primary_subject_id)
    except model.DoesNotExist as error:
        raise ValueError("Invalid library routing context") from error
    candidates = list(
        User.objects.select_related("workshop_role").filter(
            workshop_id=source.workshop_id,
            account_role=User.AccountRole.MANAGER,
            status=User.Status.ACTIVE,
            onboarding_state__isnull=True,
        )
    )
    exact = [
        user
        for user in candidates
        if user.workshop_role is not None
        and user.workshop_role.status == WorkshopRole.Status.ACTIVE
        and user.workshop_role.machine_key != "admin"
        and user.workshop_role.workshop_id in {None, source.workshop_id}
        and user.id != event.actor_user_id
    ]
    if len(exact) > 1:
        raise ValueError("Permanent manager routing is unavailable")
    if not exact:
        return []
    label = event.primary_subject_type.replace("_", " ").title()
    action = "restored" if event.event_type.endswith("_RESTORED") else "retired"
    return [
        RecipientPresentation(
            user_id=exact[0].id,
            title=f"{label} {action}",
            body=f"A Workshop library {label.lower()} was {action}.",
        )
    ]
