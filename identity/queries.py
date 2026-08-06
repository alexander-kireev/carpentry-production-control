from .models import User
from .results import Destination, DestinationResult


def resolve_authenticated_destination(user):
    if not getattr(user, "is_authenticated", False):
        return DestinationResult(Destination.LOGIN, True)
    exact_unattached_admin = (
        user.status == User.Status.ACTIVE
        and user.account_role == User.AccountRole.ADMIN
        and user.onboarding_state == User.OnboardingState.REGISTERED_NO_WORKSHOP
        and user.workshop_id is None
        and user.workshop_role_id is None
    )
    if exact_unattached_admin:
        return DestinationResult(Destination.CREATE_WORKSHOP, True)
    return DestinationResult(Destination.LOGIN, False)
