"""Workshop setup view (A2): the gate's target at ``/workshop/setup``.

Admin-only. Renders and handles the one-time Workshop setup form; on success
the service backfills ``request.user.workshop`` and the gate stops firing, so
the user is redirected to their dashboard. A non-admin who somehow reaches this
route has no MVP Workshop-creation path, so they get a clean 403 rather than a
new flow (design note, ticket A2).
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from accounts.models import User
from workshop.forms import WorkshopSetupForm
from workshop.services import WorkshopExistsError, create_workshop


@login_required
def setup(request):
    if request.user.account_role != User.AccountRole.ADMIN:
        # Non-admins have no Workshop-creation path (A2 design note): a clean
        # refusal, not a redirect — the gate already routed them here.
        raise PermissionDenied

    if request.user.workshop_id is not None:
        # Already set up — the gate no longer applies; send them to the dashboard.
        return redirect("/")

    if request.method == "POST":
        form = WorkshopSetupForm(request.POST)
        if form.is_valid():
            try:
                create_workshop(form, request.user)
            except WorkshopExistsError:
                # Race backstop only — the guard above already handled the
                # normal already-set-up case.
                return redirect("/")
            return redirect("/")
    else:
        form = WorkshopSetupForm()

    return render(request, "workshop/setup.html", {"form": form})
