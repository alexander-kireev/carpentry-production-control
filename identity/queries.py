from workshops.models import Workshop, WorkshopRole

from .models import EmailDeliveryIntent, User, UserInvitation
from .results import Destination, DestinationResult


def resolve_authenticated_destination(user):
    if not getattr(user, "is_authenticated", False):
        return DestinationResult(Destination.LOGIN, True)
    try:
        user = User.objects.select_related("workshop", "workshop_role").get(pk=user.pk)
    except User.DoesNotExist:
        return DestinationResult(Destination.LOGIN, False)
    exact_unattached_admin = (
        user.status == User.Status.ACTIVE
        and user.account_role == User.AccountRole.ADMIN
        and user.onboarding_state == User.OnboardingState.REGISTERED_NO_WORKSHOP
        and user.workshop_id is None
        and user.workshop_role_id is None
    )
    if exact_unattached_admin:
        return DestinationResult(Destination.CREATE_WORKSHOP, True, user=user)
    if not (
        user.status == User.Status.ACTIVE
        and user.onboarding_state is None
        and user.workshop_id is not None
        and user.workshop_role_id is not None
    ):
        return DestinationResult(Destination.LOGIN, False, user=user)

    workshop = user.workshop
    role = user.workshop_role
    exact_admin = (
        user.account_role == User.AccountRole.ADMIN
        and role.workshop_id is None
        and role.machine_key == "admin"
        and role.name == "Admin"
        and role.status == WorkshopRole.Status.ACTIVE
    )
    lawful_non_admin = (
        user.account_role in {User.AccountRole.MANAGER, User.AccountRole.OPERATOR}
        and role.status == WorkshopRole.Status.ACTIVE
        and role.machine_key != "admin"
        and role.workshop_id in {None, workshop.id}
    )
    if not (exact_admin or lawful_non_admin):
        return DestinationResult(Destination.LOGIN, False, user=user)

    if workshop.status == Workshop.Status.MANAGER_REQUIRED:
        if exact_admin:
            return DestinationResult(Destination.INVITE_MANAGER, True, user=user)
        if user.account_role == User.AccountRole.OPERATOR:
            return DestinationResult(Destination.HOLDING, True, user=user)
        return DestinationResult(Destination.LOGIN, False, user=user)
    if workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING:
        if exact_admin:
            return DestinationResult(Destination.SETUP_COCKPIT, True, user=user)
        if user.account_role == User.AccountRole.OPERATOR:
            return DestinationResult(Destination.HOLDING, True, user=user)
        return DestinationResult(Destination.LOGIN, False, user=user)
    if workshop.status == Workshop.Status.OPERATIONAL:
        return DestinationResult(
            Destination.DASHBOARD,
            True,
            role_home=user.account_role,
            user=user,
        )
    return DestinationResult(Destination.LOGIN, False, user=user)


def get_pending_manager_setup(user):
    if not (
        user.account_role == User.AccountRole.ADMIN
        and user.status == User.Status.ACTIVE
        and user.workshop_id is not None
        and user.workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING
    ):
        return None
    candidates = list(
        User.objects.filter(
            workshop_id=user.workshop_id,
            account_role=User.AccountRole.MANAGER,
            status=User.Status.PENDING,
            onboarding_state__isnull=True,
        ).select_related("workshop_role")
    )
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    if not (
        candidate.workshop_role is not None
        and candidate.workshop_role.workshop_id is None
        and candidate.workshop_role.machine_key == "undefined"
    ):
        return None
    invitations = list(
        UserInvitation.objects.filter(
            user=candidate,
            workshop_id=user.workshop_id,
            status=UserInvitation.Status.PENDING,
        )
    )
    if len(invitations) != 1:
        return None
    invitation = invitations[0]
    intents = list(
        EmailDeliveryIntent.objects.filter(
            invitation=invitation,
            invitation_generation=invitation.invitation_generation,
        )
    )
    if len(intents) != 1:
        return None
    intent = intents[0]
    return {
        "workshop_name": user.workshop.name,
        "workshop_timezone": user.workshop.timezone,
        "candidate_name": f"{candidate.first_name} {candidate.last_name}",
        "candidate_email": candidate.email,
        "expires_at": invitation.expires_at,
        "delivery_status": intent.status,
    }
