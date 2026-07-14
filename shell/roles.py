"""Role helpers shared by dispatch, the effective-role context, and the DEBUG switcher.

Single source of truth for "which role drives the UI" so ``root`` dispatch, the
``effective_role`` context processor, and the DEBUG switcher can't drift apart.
"""

from django.conf import settings

from accounts.models import User

# Landing route name per account role.
ROLE_LANDING = {
    User.AccountRole.ADMIN: "admin_dashboard",
    User.AccountRole.MANAGER: "manager_dashboard",
    User.AccountRole.OPERATOR: "operator_dashboard",
    User.AccountRole.TECHNICIAN: "technician_dashboard",
}

# Session key holding a DEBUG-only "view as role" override.
OVERRIDE_SESSION_KEY = "debug_override_role"


def get_effective_role(request):
    """Return the role driving the UI for this request.

    Normally the user's ``account_role``. When ``settings.DEBUG`` is true, a
    ``debug_override_role`` session value (set by the DEBUG switcher) takes
    precedence — so the switcher is completely inert in production.
    """
    if not request.user.is_authenticated:
        return None
    role = request.user.account_role
    if settings.DEBUG:
        override = request.session.get(OVERRIDE_SESSION_KEY)
        if override in User.AccountRole.values:
            role = override
    return role
