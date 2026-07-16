"""Notifications page + nav badge (N2).

Covers list scoping (own only), the unread badge, the read/unread/dismiss
transitions, Mark-all-read (effect + flags/dismissed untouched), pin/important
independence and the filter tabs, the Linked Context panel (registered vs NULL
source), select/open behaviour, and the cross-user permission boundary — all
driven by ``NotificationFactory`` (no live triggers; that is N3).

Runs against PostgreSQL (config.settings.test) — never SQLite. ``UserFactory``
gives every user a workshop by default, so ``force_login`` clears the A2 setup
gate without special handling.
"""

import pytest

from notifications.models import Notification
from tests.factories import ChangeRequestFactory, NotificationFactory, UserFactory

pytestmark = pytest.mark.django_db


# --- Access + list scoping -----------------------------------------------


def test_notifications_requires_login(client):
    response = client.get("/notifications")
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_lists_only_own_notifications(client):
    user = UserFactory(account_role="operator")
    NotificationFactory(recipient=user, title="Mine to see")
    NotificationFactory(recipient=UserFactory(), title="Someone else's")
    client.force_login(user)
    content = client.get("/notifications").content.decode()
    assert "Mine to see" in content
    assert "Someone else's" not in content


def test_empty_state_when_none(client):
    client.force_login(UserFactory(account_role="operator"))
    content = client.get("/notifications").content.decode()
    assert "You have no notifications." in content


def test_unread_item_is_visually_distinct(client):
    user = UserFactory()
    NotificationFactory(recipient=user)  # unread (steady state)
    client.force_login(user)
    content = client.get("/notifications").content.decode()
    assert "notif-item--unread" in content


def test_dismissed_notifications_are_excluded_from_the_active_list(client):
    user = UserFactory()
    NotificationFactory(recipient=user, dismissed=True, title="Gone from view")
    client.force_login(user)
    content = client.get("/notifications").content.decode()
    assert "Gone from view" not in content


def test_no_leaked_template_comments(client):
    # Multi-line {# #} is not a valid Django comment and would leak as literal
    # text; the templates use {% comment %} — guard it (the S1 rule).
    user = UserFactory()
    NotificationFactory(recipient=user)
    client.force_login(user)
    content = client.get("/notifications").content.decode()
    assert "{#" not in content
    assert "{% comment %}" not in content


# --- Nav unread badge -----------------------------------------------------


def test_badge_counts_unread_only(client):
    user = UserFactory(account_role="operator")
    NotificationFactory.create_batch(2, recipient=user)  # unread
    NotificationFactory(recipient=user, read=True)  # read — not counted
    client.force_login(user)
    content = client.get("/operator").content.decode()
    assert 'notif-nav-badge">2</span>' in content


@pytest.mark.parametrize(
    "role, landing",
    [
        ("admin", "/admin"),
        ("manager", "/manager"),
        ("operator", "/operator"),
        ("technician", "/tech"),
    ],
)
def test_badge_renders_on_every_role_nav(client, role, landing):
    user = UserFactory(account_role=role)
    NotificationFactory(recipient=user)  # one unread
    client.force_login(user)
    content = client.get(landing).content.decode()
    assert 'notif-nav-badge">1</span>' in content


def test_badge_hidden_when_no_unread(client):
    user = UserFactory(account_role="operator")
    NotificationFactory(recipient=user, read=True)
    client.force_login(user)
    content = client.get("/operator").content.decode()
    # The element still renders (so HTMX OOB can target it) but is hidden.
    assert "notif-nav-badge d-none" in content


def test_badge_context_processor_safe_for_anonymous(client):
    # Runs on every request, including the anonymous login page — must not query
    # AnonymousUser.notifications.
    assert client.get("/login").status_code == 200


def test_badge_updates_after_marking_read(client):
    user = UserFactory(account_role="operator")
    notification = NotificationFactory(recipient=user)
    client.force_login(user)
    # The HTMX action response carries the refreshed badge as an OOB swap.
    response = client.post(f"/notifications/{notification.pk}/read")
    content = response.content.decode()
    assert 'hx-swap-oob="true"' in content
    assert "notif-nav-badge d-none" in content  # now zero unread


# --- Mark all read --------------------------------------------------------


def test_mark_all_read_reads_unread_and_leaves_flags_and_dismissed(client):
    user = UserFactory(account_role="manager")
    plain = NotificationFactory(recipient=user)
    flagged = NotificationFactory(recipient=user, important=True, pinned=True)
    dismissed = NotificationFactory(recipient=user, dismissed=True)
    client.force_login(user)

    response = client.post("/notifications/mark-all-read")
    assert response.status_code == 302
    assert response["Location"] == "/notifications"

    plain.refresh_from_db()
    flagged.refresh_from_db()
    dismissed.refresh_from_db()
    assert plain.status == Notification.Status.READ
    assert flagged.status == Notification.Status.READ
    # Flags survive; the dismissed record is untouched.
    assert flagged.important and flagged.pinned
    assert dismissed.status == Notification.Status.DISMISSED


# --- Per-item transitions -------------------------------------------------


def test_mark_read_persists(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user)
    client.force_login(user)
    client.post(f"/notifications/{notification.pk}/read")
    notification.refresh_from_db()
    assert notification.status == Notification.Status.READ


def test_mark_unread_persists(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user, read=True)
    client.force_login(user)
    client.post(f"/notifications/{notification.pk}/unread")
    notification.refresh_from_db()
    assert notification.status == Notification.Status.UNREAD


def test_dismiss_persists(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user)
    client.force_login(user)
    client.post(f"/notifications/{notification.pk}/dismiss")
    notification.refresh_from_db()
    assert notification.status == Notification.Status.DISMISSED


# --- Personal flags: independent of status, drive the tabs ----------------


def test_pin_toggles_without_changing_status(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user)  # unread
    client.force_login(user)

    client.post(f"/notifications/{notification.pk}/pin")
    notification.refresh_from_db()
    assert notification.pinned is True
    assert notification.status == Notification.Status.UNREAD  # unchanged

    client.post(f"/notifications/{notification.pk}/pin")
    notification.refresh_from_db()
    assert notification.pinned is False


def test_important_toggles_without_changing_status(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user, read=True)
    client.force_login(user)

    client.post(f"/notifications/{notification.pk}/important")
    notification.refresh_from_db()
    assert notification.important is True
    assert notification.status == Notification.Status.READ  # unchanged


def test_pinned_tab_shows_only_pinned(client):
    user = UserFactory()
    NotificationFactory(recipient=user, pinned=True, title="Pinned one")
    NotificationFactory(recipient=user, title="Plain one")
    client.force_login(user)
    content = client.get("/notifications?filter=pinned").content.decode()
    assert "Pinned one" in content
    assert "Plain one" not in content


def test_important_tab_shows_only_important(client):
    user = UserFactory()
    NotificationFactory(recipient=user, important=True, title="Important one")
    NotificationFactory(recipient=user, title="Plain one")
    client.force_login(user)
    content = client.get("/notifications?filter=important").content.decode()
    assert "Important one" in content
    assert "Plain one" not in content


def test_unread_tab_shows_only_unread(client):
    user = UserFactory()
    NotificationFactory(recipient=user, title="Still unread")
    NotificationFactory(recipient=user, read=True, title="Already read")
    client.force_login(user)
    content = client.get("/notifications?filter=unread").content.decode()
    assert "Still unread" in content
    assert "Already read" not in content


def test_pinned_but_dismissed_is_absent_from_pinned_tab(client):
    # Flags are valid in any state, but dismissed is cleared from the active
    # list everywhere — including the Pinned tab.
    user = UserFactory()
    NotificationFactory(recipient=user, pinned=True, dismissed=True, title="Pinned+dismissed")
    client.force_login(user)
    content = client.get("/notifications?filter=pinned").content.decode()
    assert "Pinned+dismissed" not in content


# --- Linked Context panel -------------------------------------------------


def test_select_shows_linked_context_for_registered_source(client):
    user = UserFactory()
    cr = ChangeRequestFactory()
    notification = NotificationFactory(
        recipient=user, source_type="ChangeRequest", source_id=str(cr.pk)
    )
    client.force_login(user)
    content = client.post(f"/notifications/{notification.pk}/select").content.decode()
    # The live summary from N1's registry (the CR's business code + proposed value).
    assert cr.code in content
    assert cr.proposed_value in content


def test_select_shows_nothing_for_null_source(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user)  # no source
    client.force_login(user)
    content = client.post(f"/notifications/{notification.pk}/select").content.decode()
    assert "No linked context for this notification." in content


def test_select_marks_read(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user)  # unread
    client.force_login(user)
    client.post(f"/notifications/{notification.pk}/select")
    notification.refresh_from_db()
    assert notification.status == Notification.Status.READ


# --- Open: navigate to base target + mark read ----------------------------


def test_open_marks_read_and_redirects_to_own_profile(client):
    user = UserFactory()
    notification = NotificationFactory(
        recipient=user,
        category=Notification.Category.ACCOUNT,
        source_type="User",
        source_id=str(user.pk),
    )
    client.force_login(user)
    response = client.get(f"/notifications/{notification.pk}/open")
    assert response.status_code == 302
    assert response["Location"] == "/profile"
    notification.refresh_from_db()
    assert notification.status == Notification.Status.READ


def test_open_without_base_target_falls_back_to_the_list(client):
    user = UserFactory()
    notification = NotificationFactory(recipient=user)  # no resolvable source page
    client.force_login(user)
    response = client.get(f"/notifications/{notification.pk}/open")
    assert response.status_code == 302
    assert response["Location"] == "/notifications"
    notification.refresh_from_db()
    assert notification.status == Notification.Status.READ


# --- Permission: cannot touch another user's notifications ----------------


def test_cannot_act_on_another_users_notification(client):
    owner = UserFactory()
    other = UserFactory(account_role="operator")
    notification = NotificationFactory(recipient=owner)
    client.force_login(other)
    assert client.post(f"/notifications/{notification.pk}/dismiss").status_code == 404
    notification.refresh_from_db()
    assert notification.status == Notification.Status.UNREAD  # unchanged


def test_cannot_select_another_users_notification(client):
    owner = UserFactory()
    other = UserFactory(account_role="operator")
    notification = NotificationFactory(recipient=owner)
    client.force_login(other)
    assert client.post(f"/notifications/{notification.pk}/select").status_code == 404
