from datetime import date

import pytest
from django.test import Client

from identity.models import User, WorkshopCreationCommandReceipt
from workshops.models import OperationType, WorkshopRole

pytestmark = pytest.mark.django_db(transaction=True)


def admin():
    WorkshopRole.objects.get_or_create(
        machine_key="undefined", defaults={"name": "undefined", "status": "active"}
    )
    WorkshopRole.objects.get_or_create(
        machine_key="admin", defaults={"name": "Admin", "status": "active"}
    )
    OperationType.objects.get_or_create(
        machine_key="other",
        defaults={
            "name": "Other",
            "is_production": True,
            "requires_clearance": False,
            "status": "active",
        },
    )
    return User.objects.create_user(
        email="http-creator@example.test",
        password="Valid-password-483!",
        first_name="HTTP",
        last_name="Creator",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )


def test_create_workshop_page_and_post_success(client):
    user = admin()
    client.force_login(user)
    page = client.get("/onboarding/workshop")
    assert page.status_code == 200
    assert (
        b"Create your workshop" in page.content and b"data-submit-once" in page.content
    )
    form = page.context["form"]
    response = client.post(
        "/onboarding/workshop",
        {
            "submission_nonce": form.initial["submission_nonce"],
            "expected_user_version": form.initial["expected_user_version"],
            "name": "HTTP Workshop",
            "address": "1 HTTP Lane",
            "contact_email": "contact@example.test",
            "timezone": "Europe/London",
        },
    )
    assert response.headers["Location"] == "/onboarding/manager"
    handoff = client.get(response.headers["Location"])
    assert b"Workshop saved" in handoff.content
    assert b'id="id_first_name"' not in handoff.content
    assert b"generation" not in handoff.content.lower()
    assert WorkshopCreationCommandReceipt.objects.count() == 1
    assert (
        client.get("/onboarding/workshop").headers["Location"] == "/onboarding/manager"
    )


def test_csrf_and_trailing_slashes_are_rejected(client):
    user = admin()
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert secure.post("/onboarding/workshop", {}).status_code == 403
    assert client.get("/onboarding/workshop/").status_code in {301, 302, 404}


def test_validation_retains_non_secret_fields(client):
    user = admin()
    client.force_login(user)
    page = client.get("/onboarding/workshop")
    response = client.post(
        "/onboarding/workshop",
        {
            "submission_nonce": page.context["form"].initial["submission_nonce"],
            "expected_user_version": 1,
            "name": "Kept",
            "address": "",
            "contact_email": "bad",
            "timezone": "Not/AZone",
        },
    )
    assert response.status_code == 400 and b"Kept" in response.content
    assert b"Select a valid choice" in response.content
