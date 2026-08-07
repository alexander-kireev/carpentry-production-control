import re

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.urls import reverse

from events.models import Event
from identity.models import User
from tests.test_library_commands import library_admin
from workshops.commands import (
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
        "templates/onboarding/setup_cockpit.html",
        "templates/onboarding/_timezone_correction.html",
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
    mobile = css.split("@media (max-width: 40rem)", 1)[1]
    close_rule = re.search(r"\.library-dialog-close\s*\{([^}]*)\}", mobile)
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
        "aria-live" in content
        and "<caption>" in content
        and "Return to setup status" in content
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
    assert "Add from presets — unavailable" in content
    assert 'disabled aria-disabled="true"' in content
    assert '<dialog class="library-dialog"' in content
    assert not any(token in content for token in ("Ã", "Â", "â€"))


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
    assert "Read-only catalogue" in content
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
    assert 'value="active" selected' in body
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
