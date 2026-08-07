import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from identity.models import User
from tests.test_material_commands import material_data, material_dependencies
from tests.test_material_queries import operational_actor
from workshops.commands import (
    create_material,
    transition_material,
    transition_material_variant,
)
from workshops.models import Workshop

pytestmark = pytest.mark.django_db(transaction=True)


def _assert_filters_retained(content, *, query, status, category_id):
    assert re.search(rf'name="q"[^>]*value="{re.escape(query)}"', content)
    assert re.search(rf'<option value="{status}"[^>]*selected', content)
    assert re.search(rf'<option value="{category_id}"[^>]*selected', content)


def _mutation_actions(content):
    return [
        unescape(action)
        for action in re.findall(r'<form[^>]+action="([^"]+)"', content)
        if "/materials/" in action
    ]


def test_pending_admin_page_is_accessible_and_preset_is_disabled():
    actor, _, _, _ = material_dependencies("http-pending")
    client = Client()
    client.force_login(actor)
    response = client.get(reverse("workshops:materials"))
    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "Return to setup status" in content
    assert "Add material" in content
    assert "Add from presets — unavailable" in content
    assert re.search(r"<button[^>]*disabled[^>]*>Add from presets", content)
    assert "data-submit-once" in content
    assert 'role="status" aria-live="polite"' in content
    assert client.get("/workshop/materials/").status_code in {302, 404}


def test_create_validation_reopens_bound_dialog_and_writes_nothing():
    actor, workshop, category, unit = material_dependencies("http-invalid")
    client = Client()
    client.force_login(actor)
    response = client.post(
        reverse("workshops:material-create"),
        {
            "submission_key": "4ecacee9-44e5-48e5-a1c5-4f387321f290",
            "name": "",
            "category_id": category.id,
            "category_version": category.version,
            "unit_id": unit.id,
            "unit_version": unit.version,
        },
    )
    content = response.content.decode("utf-8")
    assert response.status_code == 400
    assert 'id="create-material"' in content
    assert "data-dialog-auto-open" in content
    assert "Nothing was saved" in content
    assert workshop.materials.count() == 0


def test_edit_validation_reopens_the_exact_bound_dialog():
    actor, workshop, category, unit = material_dependencies("http-edit-invalid")
    created = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data=material_data(category, unit),
    )
    client = Client()
    client.force_login(actor)
    response = client.post(
        reverse("workshops:material-edit", args=(created.material_id,)),
        {
            "submission_key": "29965165-1cbf-4144-b6da-822260921bdd",
            "version": 1,
            "name": "",
            "category_id": category.id,
            "category_version": category.version,
            "unit_id": unit.id,
            "unit_version": unit.version,
        },
    )
    content = response.content.decode("utf-8")
    assert response.status_code == 400
    opening = re.search(
        rf'<dialog[^>]*id="edit-material-{created.material_id}"[^>]*>', content
    ).group(0)
    assert "data-dialog-auto-open" in opening
    assert workshop.materials.get().name == "Birch plywood"


def test_all_material_mutation_actions_preserve_active_filters():
    actor, workshop, category, unit = material_dependencies("http-actions")
    created = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data=material_data(
            category,
            unit,
            spec_label="18 mm",
            opening_quantity="2",
            min_threshold="1",
        ),
    )
    client = Client()
    client.force_login(actor)
    query = {"q": "Birch plywood", "status": "active", "category": category.id}
    active = client.get(reverse("workshops:materials"), query).content.decode("utf-8")
    active_actions = _mutation_actions(active)
    expected_active_paths = {
        reverse("workshops:material-create"),
        reverse("workshops:material-edit", args=(created.material_id,)),
        reverse("workshops:material-transition", args=(created.material_id, "retire")),
        reverse("workshops:material-variant-create", args=(created.material_id,)),
        reverse("workshops:material-variant-edit", args=(created.variant_id,)),
        reverse(
            "workshops:material-variant-transition",
            args=(created.variant_id, "retire"),
        ),
    }
    assert expected_active_paths <= {urlsplit(action).path for action in active_actions}
    for action in active_actions:
        assert parse_qs(urlsplit(action).query) == {
            "q": ["Birch plywood"],
            "status": ["active"],
            "category": [str(category.id)],
        }

    retired_variant = transition_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        variant_id=created.variant_id,
        expected_version=2,
        idempotency_key="retire-variant",
        action="archive",
    )
    retired_material = transition_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=created.material_id,
        expected_version=1,
        idempotency_key="retire-material",
        action="archive",
    )
    assert (retired_variant.code, retired_material.code) == ("committed", "committed")
    query["status"] = "archived"
    archived = client.get(reverse("workshops:materials"), query).content.decode("utf-8")
    archived_actions = _mutation_actions(archived)
    expected_restore_paths = {
        reverse("workshops:material-transition", args=(created.material_id, "restore")),
    }
    assert expected_restore_paths <= {
        urlsplit(action).path for action in archived_actions
    }
    for action in archived_actions:
        assert parse_qs(urlsplit(action).query) == {
            "q": ["Birch plywood"],
            "status": ["archived"],
            "category": [str(category.id)],
        }

    restored_material = transition_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=created.material_id,
        expected_version=2,
        idempotency_key="restore-material",
        action="restore",
    )
    assert restored_material.code == "committed"
    query["status"] = "active"
    variant_restore = client.get(reverse("workshops:materials"), query).content.decode(
        "utf-8"
    )
    restore_actions = _mutation_actions(variant_restore)
    assert reverse(
        "workshops:material-variant-transition",
        args=(created.variant_id, "restore"),
    ) in {urlsplit(action).path for action in restore_actions}
    for action in restore_actions:
        assert parse_qs(urlsplit(action).query) == {
            "q": ["Birch plywood"],
            "status": ["active"],
            "category": [str(category.id)],
        }


def test_success_invalid_stale_and_blocked_mutations_retain_filters():
    actor, workshop, category, unit = material_dependencies("http-retention")
    created = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data=material_data(
            category,
            unit,
            spec_label="Standard",
            opening_quantity="2",
            min_threshold="1",
        ),
    )
    client = Client()
    client.force_login(actor)
    query = f"?q=Birch&status=active&category={category.id}"

    successful = client.post(
        reverse("workshops:material-edit", args=(created.material_id,)) + query,
        {
            "submission_key": "a3bf356e-53ea-4319-92ab-e58c5a0b88a7",
            "version": 1,
            "name": "Birch veneer",
            "category_id": category.id,
            "category_version": category.version,
            "unit_id": unit.id,
            "unit_version": unit.version,
        },
    )
    assert successful.status_code == 200
    _assert_filters_retained(
        successful.content.decode("utf-8"),
        query="Birch",
        status="active",
        category_id=category.id,
    )

    invalid = client.post(
        reverse("workshops:material-variant-create", args=(created.material_id,))
        + query,
        {
            "submission_key": "d28794ab-aa25-4433-8916-78238f288ffe",
            "material_version": 2,
            "spec_label": "",
        },
    )
    assert invalid.status_code == 400
    _assert_filters_retained(
        invalid.content.decode("utf-8"),
        query="Birch",
        status="active",
        category_id=category.id,
    )

    stale = client.post(
        reverse("workshops:material-variant-edit", args=(created.variant_id,)) + query,
        {
            "submission_key": "99cc2115-4e41-4880-b842-092d1421aaf0",
            "version": 99,
            "spec_label": "Standard",
            "min_threshold": "1",
        },
    )
    assert stale.status_code == 400
    assert "This item changed" in stale.content.decode("utf-8")
    _assert_filters_retained(
        stale.content.decode("utf-8"),
        query="Birch",
        status="active",
        category_id=category.id,
    )

    blocked = client.post(
        reverse("workshops:material-transition", args=(created.material_id, "retire"))
        + query,
        {
            "submission_key": "79761b1a-ddd8-4aa7-af21-43be0a78eb5c",
            "version": 2,
        },
    )
    assert blocked.status_code == 400
    assert "dependency blocks" in blocked.content.decode("utf-8")
    _assert_filters_retained(
        blocked.content.decode("utf-8"),
        query="Birch",
        status="active",
        category_id=category.id,
    )


@pytest.mark.parametrize(
    "account_role", (User.AccountRole.MANAGER, User.AccountRole.OPERATOR)
)
def test_readonly_roles_see_safe_facts_and_no_mutation_or_preset_controls(account_role):
    admin, workshop, category, unit = material_dependencies(f"http-{account_role}")
    created = create_material(
        actor_id=admin.id,
        workshop_id=workshop.id,
        submission_key="create",
        data=material_data(
            category,
            unit,
            spec_label="Standard",
            opening_quantity="2",
            min_threshold="1",
        ),
    )
    workshop.status = Workshop.Status.OPERATIONAL
    workshop.save(update_fields=("status",))
    viewer = operational_actor(
        workshop,
        f"HTTP {account_role}",
        account_role,
        f"http-{account_role}@example.test",
    )
    client = Client()
    client.force_login(viewer)
    content = client.get(reverse("workshops:materials")).content.decode("utf-8")
    assert all(text in content for text in ("Current", "Reserved", "Available"))
    assert all(
        text not in content
        for text in (
            "Add material",
            "Add from presets",
            "Save changes",
            "Confirm retirement",
            'data-dialog-open="retire-',
        )
    )
    denied = client.post(
        reverse("workshops:material-transition", args=(created.material_id, "retire")),
        {"version": 1, "submission_key": "ac07cbd2-a91d-4ee8-a223-50ee76473855"},
    )
    assert denied.status_code == 302


def test_cross_tenant_detail_is_non_disclosing():
    actor, _, category, unit = material_dependencies("detail-source")
    created = create_material(
        actor_id=actor.id,
        workshop_id=actor.workshop_id,
        submission_key="create",
        data=material_data(category, unit),
    )
    foreign, *_ = material_dependencies("detail-foreign")
    client = Client()
    client.force_login(foreign)
    response = client.get(
        reverse("workshops:material-detail", args=(created.material_id,))
    )
    assert response.status_code == 404
