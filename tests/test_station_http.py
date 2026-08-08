import re
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from identity.models import User
from tests.test_cross_role_visual_conformance import _operational_people
from tests.test_library_commands import library_admin
from workshops.models import OperationType, Station, Workshop
from workshops.protected_configuration import resolve_protected_configuration

pytestmark = pytest.mark.django_db(transaction=True)


def test_pending_admin_create_edit_retire_prg():
    admin, workshop = library_admin("station-http")
    operation_type = OperationType.objects.create(
        workshop=workshop, name="Cutting", is_production=True, requires_clearance=True
    )
    client = Client()
    client.force_login(admin)
    created = client.post(
        reverse("workshops:station-create"),
        {
            "submission_key": str(uuid.uuid4()),
            "name": "Cell A",
            "capability_ids": [operation_type.id],
        },
    )
    assert created.status_code == 302 and created.url == reverse("workshops:stations")
    station = Station.objects.get(workshop=workshop)
    edited = client.post(
        reverse("workshops:station-edit", args=(station.code,)),
        {"version": station.version, "name": "Cell Alpha", "capability_ids": []},
    )
    assert edited.status_code == 302
    station.refresh_from_db()
    retired = client.post(
        reverse("workshops:station-retire", args=(station.code,)),
        {"version": station.version},
    )
    assert retired.status_code == 302


def test_invalid_retirement_reopens_target_dialog_without_mutation():
    admin, workshop = library_admin("station-http-invalid-retire")
    station = Station.objects.create(workshop=workshop, code="ST-001", name="Cell A")
    client = Client()
    client.force_login(admin)
    response = client.post(
        reverse("workshops:station-retire", args=(station.code,)),
        {"version": "invalid"},
        QUERY_STRING="q=Cell&lifecycle=active&availability=available&page_size=50",
    )
    station.refresh_from_db()
    html = response.content.decode("utf-8")
    assert response.status_code == 400
    assert station.lifecycle_status == Station.LifecycleStatus.ACTIVE
    assert station.availability_status == Station.AvailabilityStatus.AVAILABLE
    assert re.search(
        r'<dialog[^>]+id="retire-station-ST-001"[^>]+data-dialog-auto-open', html
    )
    assert "Nothing was saved. Correct the highlighted fields." in html
    assert 'value="Cell"' in html
    assert 'value="active"' in html
    assert 'value="available"' in html
    assert ">50</option>" in html


@pytest.mark.parametrize("index", [1, 2])
def test_operational_manager_operator_read_and_direct_post_denied(index):
    admin, manager, operator = _operational_people(f"station-http-read-{index}")
    station = Station.objects.create(
        workshop=admin.workshop, code="ST-001", name="Cell"
    )
    viewer = (manager, operator)[index - 1]
    client = Client()
    client.force_login(viewer)
    page = client.get(reverse("workshops:stations"))
    html = page.content.decode("utf-8")
    assert page.status_code == 200 and "Cell" in html and "Read-only" in html
    assert "Add station" not in html and 'data-dialog-open="edit-station' not in html
    denied = client.post(
        reverse("workshops:station-retire", args=(station.code,)), {"version": 1}
    )
    assert denied.status_code == 302


def test_filters_pagination_and_admin_metadata_are_role_safe():
    admin, manager, operator = _operational_people("station-http-filters")
    operation_type = OperationType.objects.create(
        workshop=admin.workshop,
        name="Cutting",
        is_production=True,
        requires_clearance=True,
    )
    _, foreign_workshop = library_admin("station-http-filters-foreign")
    foreign_type = OperationType.objects.create(
        workshop=foreign_workshop,
        name="Secret capability",
        is_production=True,
        requires_clearance=True,
    )
    for index in range(1, 22):
        Station.objects.create(
            workshop=admin.workshop,
            code=f"ST-{index:03d}",
            name=f"Cell {index:03d}",
            availability_status=(
                Station.AvailabilityStatus.OFFLINE
                if index == 21
                else Station.AvailabilityStatus.AVAILABLE
            ),
        )
    client = Client()
    for viewer in (manager, operator):
        client.force_login(viewer)
        invalid = client.get(
            reverse("workshops:stations"),
            {
                "q": "Cell",
                "lifecycle": "invalid",
                "availability": "invalid",
                "capability": foreign_type.id,
                "page_size": 999,
                "page": "invalid",
            },
        )
        html = invalid.content.decode("utf-8")
        assert invalid.status_code == 200
        assert "Cell 001" in html
        assert "Secret capability" not in html
        assert f'value="{foreign_type.id}"' not in html
        assert 'name="version"' not in html
        assert 'data-dialog-open="edit-station' not in html
        last = client.get(
            reverse("workshops:stations"),
            {"page": 999, "page_size": 20},
        )
        last_html = last.content.decode("utf-8")
        assert "Page 2 of 2" in last_html
        assert "Cell 021" in last_html
        assert "Cell 001" not in last_html
    assert operation_type.name == "Cutting"


def test_cross_tenant_detail_and_mutation_do_not_disclose_foreign_station():
    admin, workshop = library_admin("station-http-tenant")
    _, foreign = library_admin("station-http-tenant-foreign")
    foreign_station = Station.objects.create(
        workshop=foreign,
        code="ST-999",
        name="Secret foreign cell",
    )
    client = Client()
    client.force_login(admin)
    detail = client.get(reverse("workshops:station-detail", args=("ST-999",)))
    assert detail.status_code == 404
    response = client.post(
        reverse("workshops:station-retire", args=("ST-999",)), {"version": 1}
    )
    foreign_station.refresh_from_db()
    assert response.status_code == 400
    assert "Secret foreign cell" not in response.content.decode("utf-8")
    assert foreign_station.lifecycle_status == Station.LifecycleStatus.ACTIVE
    assert not Station.objects.filter(workshop=workshop).exists()


@pytest.mark.parametrize("role_index", [1, 2])
def test_non_admin_all_mutation_routes_fail_safely(role_index):
    admin, manager, operator = _operational_people(
        f"station-http-all-posts-{role_index}"
    )
    station = Station.objects.create(
        workshop=admin.workshop, code="ST-001", name="Cell A"
    )
    client = Client()
    client.force_login((manager, operator)[role_index - 1])
    responses = [
        client.post(
            reverse("workshops:station-create"),
            {
                "submission_key": str(uuid.uuid4()),
                "name": "Forbidden",
                "capability_ids": [],
            },
        ),
        client.post(
            reverse("workshops:station-edit", args=(station.code,)),
            {"version": 1, "name": "Forbidden", "capability_ids": []},
        ),
        client.post(
            reverse("workshops:station-retire", args=(station.code,)),
            {"version": 1},
        ),
    ]
    station.refresh_from_db()
    assert [response.status_code for response in responses] == [302, 302, 302]
    assert (station.name, station.lifecycle_status, station.version) == (
        "Cell A",
        Station.LifecycleStatus.ACTIVE,
        1,
    )
    assert not Station.objects.filter(name="Forbidden").exists()


def test_wrong_stage_and_csrf_reject_without_station_writes():
    admin, workshop = library_admin("station-http-guards-csrf")
    wrong_stage_workshop = Workshop.objects.create(
        name="Wrong stage Workshop",
        address="1 Test Street",
        email="wrong-stage@example.test",
        timezone="Europe/London",
        status=Workshop.Status.MANAGER_REQUIRED,
    )
    for name, machine_key in (
        ("Build Planning", "build_planning"),
        ("Station Maintenance", "station_maintenance"),
    ):
        OperationType.objects.create(
            workshop=wrong_stage_workshop,
            name=name,
            machine_key=machine_key,
            is_production=False,
            requires_clearance=True,
        )
    wrong_stage_admin = User.objects.create_user(
        email="admin+station-http-wrong-stage@example.test",
        password="test-only-password",
        first_name="Ada",
        last_name="Admin",
        date_of_birth="1990-04-17",
        account_role=User.AccountRole.ADMIN,
        workshop=wrong_stage_workshop,
        workshop_role=resolve_protected_configuration().admin_role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    client = Client()
    client.force_login(wrong_stage_admin)
    assert client.get(reverse("workshops:stations")).status_code == 302

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(admin)
    response = csrf_client.post(
        reverse("workshops:station-create"),
        {
            "submission_key": str(uuid.uuid4()),
            "name": "CSRF Cell",
            "capability_ids": [],
        },
    )
    assert response.status_code == 403
    assert not Station.objects.filter(workshop=workshop).exists()


def test_invalid_create_dialog_retains_values_and_accessibility_contract():
    admin, _ = library_admin("station-http-invalid-create")
    client = Client()
    client.force_login(admin)
    response = client.post(
        reverse("workshops:station-create"),
        {
            "submission_key": str(uuid.uuid4()),
            "name": "",
            "capability_ids": [],
        },
    )
    html = response.content.decode("utf-8")
    assert response.status_code == 400
    assert re.search(
        r'<dialog[^>]+id="create-station"[^>]+aria-labelledby="create-station-title"[^>]+data-dialog-auto-open',
        html,
    )
    assert 'role="alert"' in html
    assert "Nothing was saved. Correct the highlighted fields." in html
    assert "<fieldset" in html and "<legend>Supported Operation Types:</legend>" in html
    assert "data-submit-once" in html and "data-submit-status" in html
    assert not Station.objects.exists()
