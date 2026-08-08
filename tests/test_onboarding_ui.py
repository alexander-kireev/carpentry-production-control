import pytest
from django.test import Client

from tests.test_onboarding_http import _create_workshop, _reach_pending_cockpit, admin

pytestmark = pytest.mark.django_db(transaction=True)


def test_resolver_is_get_only_and_pending_home_is_manager():
    client = Client()
    user = _reach_pending_cockpit(client)
    response = client.get("/onboarding")
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/manager"
    assert client.post("/onboarding", {}).status_code == 405
    user.refresh_from_db()
    assert user.workshop_id is not None


def test_manager_required_can_revisit_workshop_but_not_setup():
    client = Client()
    user = admin()
    _create_workshop(client, user)
    assert client.get("/onboarding/workshop").status_code == 200
    assert client.get("/onboarding/manager").status_code == 200
    denied = client.get("/onboarding/setup")
    assert denied.status_code == 302
    assert denied.headers["Location"] == "/onboarding/manager"


def test_pending_admin_can_revisit_all_three_stage_destinations():
    client = Client()
    _reach_pending_cockpit(client)
    for path in ("/onboarding/workshop", "/onboarding/manager", "/onboarding/setup"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"Workshop setup" in response.content
    setup = client.get("/onboarding/setup").content
    assert b"Stations" in setup
    assert b"coming in SC-03" in setup
    assert b"Add from presets" in setup
    assert b"disabled" in setup
    manager = client.get("/onboarding/manager").content.decode("utf-8")
    assert 'href="/onboarding/manager" aria-current="page"' in manager
    assert '<span aria-current="page">' not in manager


def test_shared_interaction_contract_is_external_and_feedback_is_temporary():
    client = Client()
    user = admin()
    response = _create_workshop(client, user)
    page = client.get(response.headers["Location"])
    html = page.content.decode("utf-8")
    assert "data-toast" in html
    assert "data-toast-close" in html
    assert "/static/js/foundation.js" in html
    assert "@tabler/icons-webfont@3" in html
    assert 'class="workshop-identity" href="/onboarding/workshop"' in html
    assert "setTimeout(() => toast.remove(), remaining)" not in html


def test_shared_script_has_hard_submit_busy_dialog_and_stateful_toast_contract(
    settings,
):
    script = (settings.BASE_DIR / "static/js/foundation.js").read_text(
        encoding="utf-8", errors="strict"
    )
    assert 'form.dataset.submitting === "true"' in script
    assert "event.preventDefault()" in script
    assert 'form.setAttribute("aria-busy", "true")' in script
    assert 'setAttribute("data-in-flight", "")' in script
    assert "dialog.dataset.inFlight" in script
    assert 'dialog?.querySelectorAll("[data-dialog-close]")' in script
    assert "hovered || focused" in script
    assert "event.relatedTarget" in script

    css = (settings.BASE_DIR / "static/css/foundation.css").read_text(
        encoding="utf-8", errors="strict"
    )
    assert ".library-shell h1 { font-size: clamp(1.5rem, 4vw, 1.75rem); }" in css
