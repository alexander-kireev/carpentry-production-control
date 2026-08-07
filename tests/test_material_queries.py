import pytest

from identity.models import User
from tests.test_material_commands import material_data, material_dependencies
from workshops.commands import create_material
from workshops.models import Workshop, WorkshopRole
from workshops.queries import get_materials_catalogue, resolve_materials_access

pytestmark = pytest.mark.django_db(transaction=True)


def operational_actor(workshop, role_name, account_role, email):
    role = WorkshopRole.objects.create(workshop=workshop, name=role_name)
    return User.objects.create_user(
        email=email,
        password="test-only-password",
        first_name=role_name,
        last_name="Viewer",
        date_of_birth="1990-01-01",
        account_role=account_role,
        workshop=workshop,
        workshop_role=role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )


def test_pending_admin_gets_configuration_without_operational_stock_facts():
    actor, workshop, category, unit = material_dependencies("pending-query")
    create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data=material_data(
            category,
            unit,
            spec_label="18 mm",
            opening_quantity="3",
            min_threshold="1",
        ),
    )
    result = get_materials_catalogue(actor)
    assert result["mode"] == "admin" and result["pending_setup"] is True
    variant = result["materials"][0]["variants"][0]
    assert "version" in variant and "current_stock" not in variant


@pytest.mark.parametrize(
    "account_role", (User.AccountRole.MANAGER, User.AccountRole.OPERATOR)
)
def test_operational_viewers_receive_safe_stock_projection_only(account_role):
    admin, workshop, category, unit = material_dependencies(f"safe-{account_role}")
    create_material(
        actor_id=admin.id,
        workshop_id=workshop.id,
        submission_key="create",
        data=material_data(
            category,
            unit,
            spec_label="Standard",
            opening_quantity="4",
            min_threshold="5",
        ),
    )
    workshop.status = Workshop.Status.OPERATIONAL
    workshop.save(update_fields=("status",))
    viewer = operational_actor(
        workshop,
        f"{account_role} role",
        account_role,
        f"{account_role}@example.test",
    )
    result = get_materials_catalogue(viewer)
    assert result["mode"] == account_role
    material = result["materials"][0]
    variant = material["variants"][0]
    assert set(variant) == {
        "label",
        "status",
        "current_stock",
        "reserved",
        "available",
        "reservation_shortfall",
        "min_threshold",
        "stock_status",
    }
    assert variant["stock_status"] == "low_available"
    assert not ({"id", "version", "can_edit", "receipt"} & set(material))


def test_search_filter_order_and_cross_tenant_resolution():
    admin, workshop, category, unit = material_dependencies("filter")
    for key, name in (("b", "Birch"), ("a", "Ash")):
        create_material(
            actor_id=admin.id,
            workshop_id=workshop.id,
            submission_key=key,
            data=material_data(category, unit) | {"name": name},
        )
    result = get_materials_catalogue(admin, search="Ash", status="active")
    assert [row["name"] for row in result["materials"]] == ["Ash"]
    foreign, *_ = material_dependencies("other-workshop")
    assert resolve_materials_access(foreign).workshop.id != workshop.id
    assert all(
        row["name"] != "Ash" for row in get_materials_catalogue(foreign)["materials"]
    )
