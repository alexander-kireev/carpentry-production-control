import re
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.urls import reverse

from events.models import Event
from identity.models import User
from tests.test_library_commands import library_admin
from workshops.commands import (
    FAMILY_MODELS,
    create_library_item,
    edit_library_item,
    transition_library_item,
)
from workshops.models import (
    ConfigurationCommandReceipt,
    MaterialCategory,
    OperationType,
    ShiftDefinition,
    UnitType,
    Workshop,
    WorkshopRole,
)
from workshops.protected_configuration import resolve_protected_configuration

pytestmark = pytest.mark.django_db(transaction=True)


def test_relevant_rendered_templates_are_strict_utf8_without_mojibake():
    paths = (
        "templates/base.html",
        "templates/workshops/libraries_admin.html",
        "templates/workshops/libraries_manager.html",
        "templates/workshops/_library_family.html",
        "templates/onboarding/workshop_setup.html",
        "templates/onboarding/workshop_details.html",
    )
    for relative_path in paths:
        content = (settings.BASE_DIR / relative_path).read_text(
            encoding="utf-8", errors="strict"
        )
        assert not any(token in content for token in ("Ã", "Â", "â€"))


def test_mobile_dialog_close_retains_compact_accessible_touch_target():
    css = (settings.BASE_DIR / "static/css/foundation.css").read_text(
        encoding="utf-8", errors="strict"
    )
    close_rule = re.search(r"\.library-dialog-close\s*\{([^}]*)\}", css)
    assert close_rule is not None and "width: 2.75rem" in close_rule.group(1)


def _mutation_form(content, route_fragment):
    for form in re.findall(r"<form\b[^>]*>.*?</form>", content, flags=re.DOTALL):
        if route_fragment in form.split(">", 1)[0]:
            return form
    raise AssertionError(f"No form found for {route_fragment}")


def _assert_submit_hook(form, message):
    opening = form.split(">", 1)[0]
    assert "data-submit-once" in opening
    statuses = re.findall(r"<span\b([^>]*)>(.*?)</span>", form, flags=re.DOTALL)
    attributes, text = next(
        (attributes, text)
        for attributes, text in statuses
        if "data-submit-status" in attributes
    )
    assert all(
        attribute in attributes
        for attribute in ('role="status"', 'aria-live="polite"', "hidden")
    )
    assert message in text


def _assert_canonical_library_navigation(client, content):
    filter_form = re.search(
        r'<form class="[^"]*library-filters[^"]*"[^>]*>',
        content,
        flags=re.DOTALL,
    ).group(0)
    assert 'method="get"' in filter_form
    assert f'action="{reverse("workshops:libraries")}"' in filter_form
    links = [
        unescape(link)
        for link in re.findall(r'<a href="([^"]+)"', content)
        if "family=" in link
    ]
    assert len(links) >= 5
    assert all(urlsplit(link).path == reverse("workshops:libraries") for link in links)
    assert all(client.get(link).status_code == 200 for link in links)


def test_every_library_mutation_form_announces_and_suppresses_duplicate_submit():
    actor, workshop = library_admin("submit-hooks")
    rows = {
        "workshop_role": WorkshopRole.objects.create(
            workshop=workshop, name="Finisher"
        ),
        "operation_type": OperationType.objects.create(
            workshop=workshop,
            name="Sanding",
            is_production=True,
            requires_clearance=True,
        ),
        "unit_type": UnitType.objects.create(
            workshop=workshop, name="Metres", abbreviation="m"
        ),
        "material_category": MaterialCategory.objects.create(
            workshop=workshop, name="Sheet goods"
        ),
        "shift_definition": ShiftDefinition.objects.create(
            workshop=workshop,
            name="Early",
            start_time="06:00",
            end_time="14:00",
            days=[0, 1],
        ),
    }
    retired = UnitType.objects.create(
        workshop=workshop,
        name="Pieces",
        abbreviation="pc",
        status=UnitType.Status.RETIRED,
    )
    client = Client()
    client.force_login(actor)
    for family, row in rows.items():
        content = client.get(f"/workshop/libraries?family={family}").content.decode()
        _assert_submit_hook(
            _mutation_form(content, f"/workshop/libraries/{family}/create"),
            "Creating custom row…",
        )
        _assert_submit_hook(
            _mutation_form(content, f"/workshop/libraries/{family}/{row.id}/edit"),
            "Saving changes…",
        )
        _assert_submit_hook(
            _mutation_form(content, f"/workshop/libraries/{family}/{row.id}/retire"),
            "Retiring row…",
        )
    unit_content = client.get("/workshop/libraries?family=unit_type").content.decode()
    _assert_submit_hook(
        _mutation_form(
            unit_content, f"/workshop/libraries/unit_type/{retired.id}/restore"
        ),
        "Restoring row…",
    )


def test_pending_admin_get_has_accessible_catalogue_and_no_trailing_slash():
    actor, _ = library_admin("http")
    client = Client()
    client.force_login(actor)
    response = client.get(reverse("workshops:libraries"))
    assert response.status_code == 200
    assert response.request["PATH_INFO"] == "/workshop/libraries"
    content = response.content.decode()
    assert (
        "<caption>" in content
        and "Workshop setup areas" in content
        and "Stations" in content
    )
    assert content.count('<section class="library-family"') == 1
    assert '<nav class="section-tabs" aria-label="Library families">' in content
    assert all(
        label in content
        for label in (
            "Roles",
            "Operation types",
            "Units",
            "Material categories",
            "Shifts",
        )
    )
    assert 'aria-current="page">Roles</a>' in content
    assert "Add from presets · SC-04" in content
    assert 'disabled aria-disabled="true"' in content
    assert '<dialog class="library-dialog dialog"' in content
    _assert_canonical_library_navigation(client, content)
    assert not any(token in content for token in ("Ã", "Â", "â€"))


@pytest.mark.parametrize(
    ("family", "payload"),
    (
        (
            "workshop_role",
            {
                "submission_key": "32e06c57-402a-4476-b49a-f9c3c06ad570",
                "name": "QA Finisher",
                "description": "Finishing role",
            },
        ),
        (
            "operation_type",
            {
                "submission_key": "a39c125d-a950-4495-845e-5e131ef3fe7c",
                "name": "QA Sanding",
                "description": "Sanding operation",
                "is_production": "on",
                "requires_clearance": "on",
            },
        ),
        (
            "unit_type",
            {
                "submission_key": "47988331-4427-4f1a-bc1a-42c779546baa",
                "name": "QA Metres",
                "abbreviation": "qm",
            },
        ),
        (
            "material_category",
            {
                "submission_key": "2ec3fffb-75d7-4620-8b92-cadbaee85d12",
                "name": "QA Sheet goods",
            },
        ),
        (
            "shift_definition",
            {
                "submission_key": "90635487-23e8-47e1-bc2a-ad6a16c725dc",
                "name": "QA Early",
                "start_time": "06:00",
                "end_time": "14:00",
                "days": ("0", "1", "2"),
            },
        ),
    ),
)
def test_all_families_use_prg_retain_filters_and_flash_success_and_replay(
    family, payload
):
    actor, workshop = library_admin(f"prg-{family}")
    client = Client()
    client.force_login(actor)
    query = f"family={family}&status=active&q=QA%20%26%20filter"
    before = client.get(f"/workshop/libraries?{query}")
    assert before.status_code == 200
    _assert_canonical_library_navigation(client, before.content.decode())

    created = client.post(
        f"/workshop/libraries/{family}/create?{query}", payload, follow=False
    )
    assert created.status_code == 302
    location = created.headers["Location"]
    assert urlsplit(location).path == reverse("workshops:libraries")
    assert parse_qs(urlsplit(location).query) == {
        "family": [family],
        "status": ["active"],
        "q": ["QA & filter"],
    }
    committed = client.get(location)
    assert committed.status_code == 200
    assert "Change committed." in committed.content.decode()
    _assert_canonical_library_navigation(client, committed.content.decode())
    assert "Change committed." not in client.get(location).content.decode()

    replay = client.post(
        f"/workshop/libraries/{family}/create?{query}", payload, follow=False
    )
    assert replay.status_code == 302 and replay.headers["Location"] == location
    recovered = client.get(location)
    assert "previously committed result was recovered" in recovered.content.decode()
    assert (
        FAMILY_MODELS[family]
        .objects.filter(workshop=workshop, name=payload["name"])
        .count()
        == 1
    )


def _queue_library_success(client, suffix):
    response = client.post(
        "/workshop/libraries/unit_type/create?family=unit_type&status=active",
        {
            "submission_key": f"00000000-0000-4000-8000-{suffix:012d}",
            "name": f"Queued unit {suffix}",
            "abbreviation": f"q{suffix}",
        },
    )
    assert response.status_code == 302


def _assert_no_queued_success(client):
    content = client.get("/workshop/libraries?family=unit_type").content.decode()
    assert "Change committed." not in content
    assert "previously committed result was recovered" not in content


def test_rejected_authorized_posts_clear_older_unfollowed_success_feedback():
    actor, workshop = library_admin("feedback-rejections")
    unit = UnitType.objects.create(workshop=workshop, name="Metres", abbreviation="m")
    role = WorkshopRole.objects.create(workshop=workshop, name="Assigned role")
    User.objects.create_user(
        email="feedback-blocker@example.test",
        password="test-only-password",
        first_name="Feedback",
        last_name="Blocker",
        date_of_birth="1992-01-01",
        account_role=User.AccountRole.OPERATOR,
        workshop=workshop,
        workshop_role=role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    _, foreign_workshop = library_admin("feedback-foreign")
    foreign = UnitType.objects.create(
        workshop=foreign_workshop, name="Foreign", abbreviation="f"
    )
    client = Client()
    client.force_login(actor)

    _queue_library_success(client, 1)
    invalid = client.post(
        "/workshop/libraries/unit_type/create?family=unit_type",
        {
            "submission_key": "00000000-0000-4000-8000-000000000101",
            "name": "Invalid unit",
            "abbreviation": "",
        },
    )
    assert invalid.status_code == 400
    _assert_no_queued_success(client)

    _queue_library_success(client, 2)
    stale = client.post(
        f"/workshop/libraries/unit_type/{unit.id}/edit?family=unit_type",
        {
            "version": 99,
            f"edit-unit_type-{unit.id}-name": "Metres",
            f"edit-unit_type-{unit.id}-abbreviation": "stale",
        },
    )
    assert stale.status_code == 200 and b"This row changed" in stale.content
    assert b"stale" in stale.content and b"data-dialog-auto-open" in stale.content
    _assert_no_queued_success(client)

    _queue_library_success(client, 3)
    blocked = client.post(
        f"/workshop/libraries/workshop_role/{role.id}/retire?family=workshop_role",
        {"version": 1},
    )
    assert blocked.status_code == 200 and b"action is unavailable" in blocked.content
    _assert_no_queued_success(client)

    _queue_library_success(client, 4)
    unavailable = client.post(
        f"/workshop/libraries/unit_type/{foreign.id}/edit?family=unit_type",
        {
            "version": 1,
            f"edit-unit_type-{foreign.id}-name": "Foreign",
            f"edit-unit_type-{foreign.id}-abbreviation": "x",
        },
    )
    assert unavailable.status_code == 400
    _assert_no_queued_success(client)


def test_feedback_is_session_local_one_time_and_denied_post_cannot_consume_it():
    actor, _ = library_admin("feedback-owner")
    _, other_workshop = library_admin("feedback-other")
    other_role = WorkshopRole.objects.create(
        workshop=other_workshop, name="Other operator"
    )
    other = User.objects.create_user(
        email="feedback-other@example.test",
        password="test-only-password",
        first_name="Other",
        last_name="Operator",
        date_of_birth="1992-01-01",
        account_role=User.AccountRole.OPERATOR,
        workshop=other_workshop,
        workshop_role=other_role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    owner = Client()
    owner.force_login(actor)
    separate = Client()
    separate.force_login(other)

    _queue_library_success(owner, 10)
    denied = separate.post(
        "/workshop/libraries/unit_type/create",
        {
            "submission_key": "00000000-0000-4000-8000-000000000011",
            "name": "Denied",
            "abbreviation": "d",
        },
    )
    assert denied.status_code == 302
    assert b"Change committed." not in denied.content
    first = owner.get("/workshop/libraries?family=unit_type").content.decode()
    second = owner.get("/workshop/libraries?family=unit_type").content.decode()
    assert "Change committed." in first
    assert "Change committed." not in second


def test_library_post_result_permission_loss_uses_safe_fallback(client, monkeypatch):
    actor, workshop = library_admin("permission-loss")
    unit = UnitType.objects.create(workshop=workshop, name="Metres", abbreviation="m")
    client.force_login(actor)
    real_resolve = __import__(
        "workshops.views", fromlist=["resolve_libraries_access"]
    ).resolve_libraries_access
    calls = 0

    def access_then_loss(user):
        nonlocal calls
        calls += 1
        return real_resolve(user) if calls == 1 else None

    monkeypatch.setattr("workshops.views.resolve_libraries_access", access_then_loss)
    monkeypatch.setattr(
        "workshops.views.edit_library_item",
        lambda **kwargs: SimpleNamespace(code="stale"),
    )
    response = client.post(
        f"/workshop/libraries/unit_type/{unit.id}/edit?family=unit_type",
        {
            "version": unit.version,
            f"edit-unit_type-{unit.id}-name": "Retained metres",
            f"edit-unit_type-{unit.id}-abbreviation": "rm",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/manager"


@pytest.mark.parametrize(
    ("family", "payload", "retained"),
    (
        (
            "workshop_role",
            {
                "submission_key": "9ff212a1-ee41-4bb0-9076-16c4cb8db84a",
                "name": "",
                "description": "Keep role detail",
            },
            "Keep role detail",
        ),
        (
            "operation_type",
            {
                "submission_key": "71069fa5-2d5a-4eac-898d-88a5bbc200f1",
                "name": "",
                "description": "Keep operation detail",
            },
            "Keep operation detail",
        ),
        (
            "unit_type",
            {
                "submission_key": "49460e0a-f6eb-48d4-8438-873d27b947ad",
                "name": "Keep unit name",
                "abbreviation": "",
            },
            "Keep unit name",
        ),
        (
            "material_category",
            {
                "submission_key": "e918a2a7-a031-42f7-9e6a-fc1e76c34a4f",
                "name": "",
            },
            "This field is required",
        ),
        (
            "shift_definition",
            {
                "submission_key": "e103d21e-91af-43e3-aa25-c1746097eb34",
                "name": "Keep shift name",
                "start_time": "14:00",
                "end_time": "06:00",
                "days": ("0",),
            },
            "Keep shift name",
        ),
    ),
)
def test_all_family_invalid_forms_stay_bound_with_canonical_navigation(
    family, payload, retained
):
    actor, _ = library_admin(f"invalid-nav-{family}")
    client = Client()
    client.force_login(actor)
    response = client.post(
        f"/workshop/libraries/{family}/create?family={family}&status=active&q=Keep",
        payload,
    )
    content = response.content.decode()
    assert response.status_code == 400
    assert retained in content
    assert f'id="create-{family}"' in content and "data-dialog-auto-open" in content
    _assert_canonical_library_navigation(client, content)


def test_role_clearances_are_accessible_checkboxes_and_submit_multiple_versions():
    actor, workshop = library_admin("role-checkboxes")
    sanding = OperationType.objects.create(
        workshop=workshop,
        name="Sanding",
        is_production=True,
        requires_clearance=True,
    )
    finishing = OperationType.objects.create(
        workshop=workshop,
        name="Finishing",
        is_production=True,
        requires_clearance=True,
    )
    client = Client()
    client.force_login(actor)
    page = client.get("/workshop/libraries?family=workshop_role")
    content = page.content.decode()
    checkboxes = re.findall(
        r'<input[^>]+type="checkbox"[^>]+name="default_clearance_ids"[^>]*>',
        content,
    )
    assert len(checkboxes) >= 4
    assert all('class="library-checkbox-list"' in item for item in checkboxes)

    response = client.post(
        "/workshop/libraries/workshop_role/create?family=workshop_role",
        {
            "submission_key": "281bfa9e-bac5-4af4-a3a1-673fb43ca7b2",
            "name": "Multi-clearance role",
            "description": "Two defaults",
            "default_clearance_ids": (str(sanding.id), str(finishing.id)),
        },
    )
    assert response.status_code == 302
    role = WorkshopRole.objects.get(workshop=workshop, name="Multi-clearance role")
    assert set(
        role.default_clearance_links.values_list("operation_type_id", flat=True)
    ) == {sanding.id, finishing.id}


def test_edit_retire_restore_prg_and_rejected_state_navigation_are_canonical():
    actor, workshop = library_admin("action-prg")
    unit = UnitType.objects.create(workshop=workshop, name="Metres", abbreviation="m")
    client = Client()
    client.force_login(actor)
    query = "family=unit_type&status=active&q=Metres"
    edited = client.post(
        f"/workshop/libraries/unit_type/{unit.id}/edit?{query}",
        {
            "version": 1,
            f"edit-unit_type-{unit.id}-name": "Metres",
            f"edit-unit_type-{unit.id}-abbreviation": "lm",
        },
    )
    assert edited.status_code == 302
    assert parse_qs(urlsplit(edited.headers["Location"]).query) == {
        "family": ["unit_type"],
        "status": ["active"],
        "q": ["Metres"],
    }
    assert (
        "Change committed." in client.get(edited.headers["Location"]).content.decode()
    )

    retired = client.post(
        f"/workshop/libraries/unit_type/{unit.id}/retire?{query}", {"version": 2}
    )
    assert retired.status_code == 302
    client.get(retired.headers["Location"])
    restored = client.post(
        f"/workshop/libraries/unit_type/{unit.id}/restore?"
        "family=unit_type&status=retired&q=Metres",
        {"version": 3},
    )
    assert restored.status_code == 302
    assert parse_qs(urlsplit(restored.headers["Location"]).query)["status"] == [
        "retired"
    ]

    stale = client.post(
        f"/workshop/libraries/unit_type/{unit.id}/edit?{query}",
        {
            "version": 1,
            f"edit-unit_type-{unit.id}-name": "Metres",
            f"edit-unit_type-{unit.id}-abbreviation": "stale",
        },
    )
    assert stale.status_code == 200
    stale_content = stale.content.decode()
    assert "This row changed" in stale_content
    assert 'value="stale"' in stale_content
    stale_dialog = re.search(
        rf'<dialog[^>]*id="edit-unit_type-{unit.id}"[^>]*>', stale_content
    ).group(0)
    assert "data-dialog-auto-open" in stale_dialog
    _assert_canonical_library_navigation(client, stale_content)

    role = WorkshopRole.objects.create(workshop=workshop, name="Assigned role")
    User.objects.create_user(
        email="assigned+action-prg@example.test",
        password="test-only-password",
        first_name="Assigned",
        last_name="Operator",
        date_of_birth="1992-01-01",
        account_role=User.AccountRole.OPERATOR,
        workshop=workshop,
        workshop_role=role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    blocked = client.post(
        f"/workshop/libraries/workshop_role/{role.id}/retire?"
        "family=workshop_role&status=active&q=Assigned",
        {"version": 1},
    )
    assert blocked.status_code == 200
    blocked_content = blocked.content.decode()
    assert "action is unavailable" in blocked_content
    _assert_canonical_library_navigation(client, blocked_content)


def test_operator_direct_get_and_post_disclose_no_library_labels():
    _, workshop = library_admin("http-operator")
    role = workshop.roles.create(name="Operator")
    operator = User.objects.create_user(
        email="operator+http@example.test",
        password="test-only-password",
        first_name="Olivia",
        last_name="Operator",
        date_of_birth="1992-06-19",
        account_role=User.AccountRole.OPERATOR,
        workshop=workshop,
        workshop_role=role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    client = Client()
    client.force_login(operator)
    for response in (
        client.get("/workshop/libraries"),
        client.post("/workshop/libraries/unit_type/create", {"name": "Secret"}),
    ):
        assert response.status_code == 302
        assert b"Libraries" not in response.content
    User.objects.filter(pk=operator.id).update(status=User.Status.INACTIVE)
    for response in (
        client.get("/workshop/libraries"),
        client.post("/workshop/libraries/unit_type/create", {"name": "Secret"}),
    ):
        assert response.status_code == 302
        assert b"Libraries" not in response.content


def test_operational_manager_is_read_only_and_has_no_internal_identifiers():
    admin, workshop = library_admin("manager-http")
    role = workshop.roles.create(name="Manager")
    manager = User.objects.create_user(
        email="manager+http@example.test",
        password="test-only-password",
        first_name="Morgan",
        last_name="Manager",
        date_of_birth="1991-05-18",
        account_role=User.AccountRole.MANAGER,
        workshop=workshop,
        workshop_role=role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    workshop.status = Workshop.Status.OPERATIONAL
    workshop.save(update_fields=["status"])
    client = Client()
    client.force_login(manager)
    content = client.get("/workshop/libraries").content.decode()
    assert "Read-only" in content
    assert (
        "library-add-button" not in content
        and "Add from presets — unavailable" not in content
        and '<dialog class="library-dialog"' not in content
        and "submission_key" not in content
        and "machine_key" not in content
    )
    denied_post = client.post(
        "/workshop/libraries/unit_type/create",
        {
            "submission_key": "0d987abb-82ed-4ff1-9df4-ded8ae9ba9f7",
            "name": "Denied",
            "abbreviation": "d",
        },
    )
    assert denied_post.status_code == 302
    assert b"Denied" not in denied_post.content
    assert UnitType.objects.filter(workshop=workshop, name="Denied").count() == 0
    client.force_login(admin)
    assert client.get("/workshop/libraries").status_code == 200


def _corrupt_protected_configuration(kind, workshop):
    with connection.cursor() as cursor:
        if kind == "global_missing":
            cursor.execute(
                "ALTER TABLE material_category DISABLE TRIGGER cst_sc01_category_guard"
            )
            try:
                cursor.execute(
                    "DELETE FROM material_category WHERE workshop_id IS NULL AND machine_key = 'undefined'"
                )
            finally:
                cursor.execute(
                    "ALTER TABLE material_category ENABLE TRIGGER cst_sc01_category_guard"
                )
            return
        cursor.execute(
            "ALTER TABLE operation_type DISABLE TRIGGER cst_046_operation_type_guard"
        )
        cursor.execute(
            "ALTER TABLE operation_type DISABLE TRIGGER cst_operation_type_no_delete"
        )
        try:
            if kind == "pair_missing":
                cursor.execute(
                    "DELETE FROM operation_type WHERE workshop_id = %s AND machine_key = 'station_maintenance'",
                    [workshop.id],
                )
            else:
                cursor.execute(
                    "UPDATE operation_type SET name = 'Malformed' WHERE workshop_id = %s AND machine_key = 'build_planning'",
                    [workshop.id],
                )
        finally:
            cursor.execute(
                "ALTER TABLE operation_type ENABLE TRIGGER cst_046_operation_type_guard"
            )
            cursor.execute(
                "ALTER TABLE operation_type ENABLE TRIGGER cst_operation_type_no_delete"
            )


@pytest.mark.parametrize(
    "corruption", ["global_missing", "pair_missing", "pair_malformed"]
)
def test_protected_configuration_corruption_fails_closed_on_get_and_every_mutation(
    corruption,
):
    actor, workshop = library_admin(f"protected-{corruption}")
    source = UnitType.objects.create(
        workshop=workshop, name="Existing", abbreviation="e"
    )
    baseline = (
        UnitType.objects.count(),
        ConfigurationCommandReceipt.objects.count(),
        Event.objects.count(),
    )
    _corrupt_protected_configuration(corruption, workshop)

    client = Client()
    client.force_login(actor)
    response = client.get("/workshop/libraries")
    assert response.status_code == 302
    assert b"Existing" not in response.content
    results = (
        create_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="unit_type",
            submission_key="denied",
            data={"name": "Denied", "abbreviation": "d"},
        ),
        edit_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="unit_type",
            item_id=source.id,
            expected_version=1,
            data={"name": "Denied edit"},
        ),
        transition_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="unit_type",
            item_id=source.id,
            expected_version=1,
            action="retire",
        ),
    )
    assert [(result.code, result.result_id) for result in results] == [
        ("unavailable", None)
    ] * 3
    source.refresh_from_db()
    assert (source.name, source.status, source.version) == ("Existing", "active", 1)
    assert (
        UnitType.objects.count(),
        ConfigurationCommandReceipt.objects.count(),
        Event.objects.count(),
    ) == baseline


def test_ordinary_extra_operation_type_does_not_break_exact_protected_pair():
    actor, workshop = library_admin("protected-extra")
    OperationType.objects.create(
        workshop=workshop,
        name="Ordinary extra",
        description="",
        is_production=False,
        requires_clearance=False,
    )
    client = Client()
    client.force_login(actor)
    assert client.get("/workshop/libraries").status_code == 200
    result = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="allowed",
        data={"name": "Allowed", "abbreviation": "a"},
    )
    assert result.code == "success"


def test_admin_edit_forms_cover_every_editable_field_and_preserve_errors_and_filters():
    actor, workshop = library_admin("edit-ui")
    role = workshop.roles.create(name="Finisher", description="Role description")
    operation = workshop.operation_types.create(
        name="Sanding",
        description="Operation description",
        is_production=True,
        requires_clearance=False,
    )
    unit = UnitType.objects.create(workshop=workshop, name="Metres", abbreviation="m")
    category = workshop.material_categories.create(name="Sheet goods")
    shift = workshop.shift_definitions.create(
        name="Early", start_time="06:00", end_time="14:00", days=[0, 1]
    )
    client = Client()
    client.force_login(actor)
    contents = {
        family: client.get(f"/workshop/libraries?family={family}").content.decode()
        for family in (
            "workshop_role",
            "operation_type",
            "unit_type",
            "material_category",
            "shift_definition",
        )
    }
    content = "".join(contents.values())
    expected_names = (
        f"edit-workshop_role-{role.id}-name",
        f"edit-workshop_role-{role.id}-description",
        f"edit-workshop_role-{role.id}-default_clearance_ids",
        f"edit-operation_type-{operation.id}-description",
        f"edit-operation_type-{operation.id}-is_production",
        f"edit-operation_type-{operation.id}-requires_clearance",
        f"edit-unit_type-{unit.id}-abbreviation",
        f"edit-material_category-{category.id}-name",
        f"edit-shift_definition-{shift.id}-start_time",
        f"edit-shift_definition-{shift.id}-end_time",
        f"edit-shift_definition-{shift.id}-days",
    )
    assert all(f'name="{name}"' in content for name in expected_names)
    assert "Operation description" in content
    assert ">Production</dt><dd>Yes" in content
    assert ">Clearance required</dt><dd>No" in content
    assert all(
        page.count('<section class="library-family"') == 1 for page in contents.values()
    )
    assert all('<dialog class="library-dialog"' in page for page in contents.values())
    assert "Confirm retirement" in content
    assert "no partial change will be saved" in content

    response = client.post(
        f"/workshop/libraries/unit_type/{unit.id}/edit?family=unit_type&status=active&q=Metres",
        {
            "version": 1,
            f"edit-unit_type-{unit.id}-name": "Metres",
            f"edit-unit_type-{unit.id}-abbreviation": "",
        },
    )
    body = response.content.decode()
    assert response.status_code == 400
    assert "This field is required" in body
    assert "data-dialog-auto-open" in body
    assert 'role="alert"' in body
    assert 'aria-current="page">Units</a>' in body
    assert 'name="family" value="unit_type"' in body
    assert 'name="status" value="active"' in body
    assert 'name="q" type="search" value="Metres"' in body
    unit.refresh_from_db()
    assert (unit.name, unit.abbreviation, unit.version) == ("Metres", "m", 1)


def test_manager_sees_complete_facts_but_no_edit_or_receipt_internals():
    _, workshop = library_admin("manager-redaction")
    role = workshop.roles.create(name="Manager")
    workshop.operation_types.create(
        name="Sanding",
        description="Safe description",
        is_production=False,
        requires_clearance=True,
    )
    manager = User.objects.create_user(
        email="manager+redaction@example.test",
        password="test-only-password",
        first_name="Morgan",
        last_name="Manager",
        date_of_birth="1991-05-18",
        account_role=User.AccountRole.MANAGER,
        workshop=workshop,
        workshop_role=role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    workshop.status = Workshop.Status.OPERATIONAL
    workshop.save(update_fields=["status"])
    client = Client()
    client.force_login(manager)
    body = client.get("/workshop/libraries?family=operation_type").content.decode()
    assert "Safe description" in body
    assert ">Production</dt><dd>No" in body
    assert ">Clearance required</dt><dd>Yes" in body
    assert all(
        token not in body
        for token in (
            "edit-operation_type",
            "submission_key",
            "payload_fingerprint",
            "result_id",
            "machine_key",
        )
    )


def test_wrong_stage_wrong_role_and_cross_tenant_routes_are_non_disclosing():
    actor, workshop = library_admin("route-matrix")
    _, foreign_workshop = library_admin("route-matrix-foreign")
    foreign = UnitType.objects.create(
        workshop=foreign_workshop, name="Foreign secret", abbreviation="fs"
    )
    client = Client()
    client.force_login(actor)
    cross = client.post(
        f"/workshop/libraries/unit_type/{foreign.id}/edit",
        {
            "version": 1,
            f"edit-unit_type-{foreign.id}-name": "Stolen",
            f"edit-unit_type-{foreign.id}-abbreviation": "s",
        },
    )
    assert cross.status_code == 400
    assert b"Foreign secret" not in cross.content
    foreign.refresh_from_db()
    assert (foreign.name, foreign.version) == ("Foreign secret", 1)

    wrong_stage = Workshop.objects.create(
        name="Wrong stage",
        address="1 Wrong Lane",
        email="wrong-stage@example.test",
        timezone="Europe/London",
        status=Workshop.Status.MANAGER_REQUIRED,
    )
    OperationType.objects.create(
        workshop=wrong_stage,
        name="Build Planning",
        is_production=False,
        requires_clearance=True,
        machine_key="build_planning",
    )
    OperationType.objects.create(
        workshop=wrong_stage,
        name="Station Maintenance",
        is_production=False,
        requires_clearance=True,
        machine_key="station_maintenance",
    )
    wrong_stage_actor = User.objects.create_user(
        email="wrong-stage@example.test",
        password="test-only-password",
        first_name="Wrong",
        last_name="Stage",
        date_of_birth="1990-01-01",
        account_role=User.AccountRole.ADMIN,
        workshop=wrong_stage,
        workshop_role=resolve_protected_configuration().admin_role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    client.force_login(wrong_stage_actor)
    for response in (
        client.get("/workshop/libraries"),
        client.post("/workshop/libraries/unit_type/create", {}),
    ):
        assert response.status_code == 302
        assert b"Foreign secret" not in response.content

    workshop.status = Workshop.Status.OPERATIONAL
    workshop.save(update_fields=["status"])
    ordinary_role = workshop.roles.create(name="Not admin")
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE user_account DISABLE TRIGGER cst_014_022_026_664_user_write_guard"
        )
        try:
            User.objects.filter(pk=actor.id).update(workshop_role=ordinary_role)
        finally:
            cursor.execute(
                "ALTER TABLE user_account ENABLE TRIGGER cst_014_022_026_664_user_write_guard"
            )
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE user_account DISABLE TRIGGER cst_014_022_026_664_user_write_guard"
        )
        try:
            client.force_login(actor)
        finally:
            cursor.execute(
                "ALTER TABLE user_account ENABLE TRIGGER cst_014_022_026_664_user_write_guard"
            )
    for response in (
        client.get("/workshop/libraries"),
        client.post("/workshop/libraries/unit_type/create", {}),
    ):
        assert response.status_code == 302
        assert b"Foreign secret" not in response.content
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 0
