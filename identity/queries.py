from django.utils import timezone

from workshops.models import Workshop, WorkshopRole

from .models import EmailDeliveryIntent, User, UserInvitation
from .results import Destination, DestinationResult, InvitationEnvelope
from .security import invitation_credential_shape, invitation_token_matches


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


def get_timezone_correction_hint(user):
    workshop = getattr(user, "workshop", None)
    eligible = bool(
        user.account_role == User.AccountRole.ADMIN
        and user.status == User.Status.ACTIVE
        and user.onboarding_state is None
        and workshop is not None
        and workshop.status
        in {
            Workshop.Status.MANAGER_REQUIRED,
            Workshop.Status.MANAGER_ACTIVATION_PENDING,
        }
        and workshop.timezone_correction_idempotency_key is None
    )
    return {
        "eligible": eligible,
        "timezone": workshop.timezone if workshop is not None else None,
        "workshop_version": workshop.version if workshop is not None else None,
    }


def get_public_invitation_envelope(selector, raw_token):
    invitation_id = invitation_credential_shape(selector, raw_token)
    unavailable = InvitationEnvelope(False)
    if invitation_id is None:
        return unavailable
    invitation = (
        UserInvitation.objects.select_related("user__workshop_role", "workshop")
        .filter(pk=invitation_id)
        .first()
    )
    if invitation is None:
        return unavailable
    candidate = invitation.user
    workshop = invitation.workshop
    role = candidate.workshop_role
    valid = (
        invitation.status == UserInvitation.Status.PENDING
        and invitation.expires_at > timezone.now()
        and invitation.token_hash_version == 1
        and invitation.user_id == candidate.id
        and invitation.workshop_id == candidate.workshop_id == workshop.id
        and candidate.account_role == User.AccountRole.MANAGER
        and candidate.status == User.Status.PENDING
        and candidate.onboarding_state is None
        and role is not None
        and role.workshop_id is None
        and role.machine_key == "undefined"
        and role.name.casefold() == "undefined"
        and role.status == WorkshopRole.Status.ACTIVE
        and workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING
        and invitation_token_matches(
            raw_token, bytes(invitation.token_salt), bytes(invitation.token_hash)
        )
    )
    if not valid:
        return unavailable
    return InvitationEnvelope(
        True,
        selector=invitation.id,
        generation=invitation.invitation_generation,
        candidate_name=f"{candidate.first_name} {candidate.last_name}",
        candidate_email=candidate.email,
        workshop_name=workshop.name,
    )
