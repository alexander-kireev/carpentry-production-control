"""Notifications page + per-item actions (N2).

The logged-in user's own notification stream (``recipient=request.user`` —
KI-021 tenancy), the Pinned/Important filter tabs, Mark-all-read, the per-item
read/unread/dismiss/pin/flag actions, and the live Linked Context panel.

This page only *drives* the N1 mechanism — the ``Notification`` transition
methods and the Linked Context registry. It never calls ``notify()`` and wires
no triggers; that is N3 (and Slices B/E). Until then the page is exercised with
factory-built notifications.

Interaction model (Option 1, confirmed at repeat-understanding): the per-item
status/flag actions are HTMX — each re-renders the list region and OOB-refreshes
the nav badge, no full reload. Selecting a row marks it read (auto-on-open) and
OOB-swaps the Linked Context panel with a live summary of the source. The panel's
"Go to source" link (shown only when a base target is resolvable) routes through
``open`` which marks read and redirects. Mark-all-read is a plain confirm-modal
POST with the post/redirect/get idiom (the D3 modal pattern).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from notifications.models import Notification
from notifications.services import linked_context

# The domain's filter model is Pinned/Important (D-124), plus All and an
# optional Unread. Every tab reads over the *active* set (dismissed excluded —
# "cleared from the active list"), so a pinned-but-dismissed row never shows.
FILTERS = ("all", "unread", "pinned", "important")


def _active_qs(user):
    """The recipient's active notifications (dismissed excluded), newest first."""
    return Notification.objects.filter(recipient=user).exclude(
        status=Notification.Status.DISMISSED
    )


def _current_filter(request):
    value = request.GET.get("filter", "all")
    return value if value in FILTERS else "all"


def _filtered_qs(request):
    """The active queryset narrowed to the current tab."""
    qs = _active_qs(request.user)
    filter_value = _current_filter(request)
    if filter_value == "unread":
        qs = qs.filter(status=Notification.Status.UNREAD)
    elif filter_value == "pinned":
        qs = qs.filter(pinned=True)
    elif filter_value == "important":
        qs = qs.filter(important=True)
    return qs


def _unread_count(user):
    """The nav-badge count — unread only, independent of the current tab."""
    return Notification.objects.filter(
        recipient=user, status=Notification.Status.UNREAD
    ).count()


def _base_target(user, notification):
    """The source object's page, or ``None`` when none is built yet.

    Deliberately minimal in Phase 1: the only always-built destination is the
    recipient's own Profile, for an ``account`` notification whose source is the
    recipient themselves (name/DOB change). Every other source's page (the
    People-tab detail, a requester's role-specific tracking surface) is delivered
    by the slice that builds it; until then the panel shows the live summary with
    no dead-end "Go to" link.
    """
    if notification.source_type == "User" and notification.source_id == str(user.pk):
        return reverse("profile")
    return None


def _list_context(request):
    """Context shared by the full page and every HTMX list re-render."""
    filter_value = _current_filter(request)
    unread_count = _unread_count(request.user)
    return {
        "notifications": _filtered_qs(request),
        "filter": filter_value,
        "unread_count": unread_count,
        "tabs": [
            ("all", "All"),
            ("unread", f"Unread ({unread_count})"),
            ("pinned", "Pinned"),
            ("important", "Important"),
        ],
    }


def _linked_context_rows(notification):
    """The source's live summary as (label, value) rows, or an empty list.

    Renders whatever the N1 registry resolves for this ``source_type`` — the keys
    differ per source (ChangeRequest vs User today; more once B/E register their
    own), so they are humanised generically rather than known here.
    """
    summary = linked_context(notification.source_type, notification.source_id)
    return [(key.replace("_", " ").capitalize(), value) for key, value in summary.items()]


def _actions_response(request, *, selected=None):
    """Re-render the list region + OOB nav badge (+ OOB Linked Context on select)."""
    context = _list_context(request)
    context["selected"] = selected
    if selected is not None:
        context["context_rows"] = _linked_context_rows(selected)
        context["base_target"] = _base_target(request.user, selected)
    return render(request, "notifications/_actions_response.html", context)


@login_required
def notifications_page(request):
    """The full Notifications page — the current tab's list + an empty panel."""
    context = _list_context(request)
    context["selected"] = None
    return render(request, "notifications/notifications.html", context)


@require_POST
@login_required
def select_notification(request, pk):
    """Select a row: mark read (auto-on-open) and load its Linked Context panel."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.status == Notification.Status.UNREAD:
        notification.mark_read()
    return _actions_response(request, selected=notification)


@login_required
def open_notification(request, pk):
    """Navigate to the notification's base target, marking it read (auto-on-open).

    A GET (a plain navigation link from the Linked Context panel). Falls back to
    the notifications page when no base target is resolvable.
    """
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.status == Notification.Status.UNREAD:
        notification.mark_read()
    return redirect(_base_target(request.user, notification) or reverse("notifications"))


@require_POST
@login_required
def mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.status != Notification.Status.DISMISSED:
        notification.mark_read()
    return _actions_response(request)


@require_POST
@login_required
def mark_unread(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.status != Notification.Status.DISMISSED:
        notification.mark_unread()
    return _actions_response(request)


@require_POST
@login_required
def dismiss(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.dismiss()
    return _actions_response(request)


@require_POST
@login_required
def toggle_pinned(request, pk):
    """Pin/unpin — a personal flag, independent of status (valid in any state)."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.set_pinned(not notification.pinned)
    return _actions_response(request)


@require_POST
@login_required
def toggle_important(request, pk):
    """Flag/unflag important — a personal flag, independent of status."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.set_important(not notification.important)
    return _actions_response(request)


@require_POST
@login_required
def mark_all_read(request):
    """Confirm-modal bulk action: every unread → read for this recipient.

    Drives the N1 transition per row (rather than a bulk ``update``) so the state
    machine stays the single authority; the active set is small in Phase 1.
    ``pinned``/``important`` and dismissed notifications are untouched — the
    filter is ``status=unread``, so no dismissed row is in scope.
    """
    for notification in Notification.objects.filter(
        recipient=request.user, status=Notification.Status.UNREAD
    ):
        notification.mark_read()
    messages.success(request, "All notifications marked as read.")
    return redirect(reverse("notifications"))
