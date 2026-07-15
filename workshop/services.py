"""Workshop service layer (A2).

Request-agnostic business logic, the same pattern as ``accounts.services`` (A1):
plain domain inputs in, a domain object out (or a domain exception) — no
``request``, session, or redirect. This keeps it callable from a view, a
management command, or a test without a fake request.
"""

from django.db import transaction

from catalog.models import Workshop


class WorkshopExistsError(Exception):
    """Raised by ``create_workshop`` when the admin already owns a Workshop.

    The one-workshop-per-admin guard lives here, not only in the view, so it
    holds even for a caller that reaches this function without the view's own
    check. It is per-admin (``admin.workshop_id``), never a global
    ``Workshop.objects.count()`` — the instance hosts many workshops (D-126).
    """


def create_workshop(form, admin) -> Workshop:
    """Create ``admin``'s Workshop from a validated form and backfill the FK.

    Refuses (``WorkshopExistsError``) if this admin already has a Workshop.
    Sets ``admin.workshop`` to the new row inside one transaction. Does not log
    in or redirect — the caller (the view) owns any HTTP/session effect.
    """
    if admin.workshop_id is not None:
        raise WorkshopExistsError

    with transaction.atomic():
        workshop = form.save()
        admin.workshop = workshop
        admin.save(update_fields=["workshop"])

    return workshop
