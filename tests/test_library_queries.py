import pytest

from identity.models import User
from tests.test_library_commands import library_admin
from workshops.models import UnitType, Workshop
from workshops.queries import get_libraries_catalogue

pytestmark = pytest.mark.django_db(transaction=True)


def test_admin_projection_and_search_are_workshop_scoped():
    actor, workshop = library_admin("query")
    UnitType.objects.create(workshop=workshop, name="Metres", abbreviation="m")
    _, other = library_admin("foreign")
    UnitType.objects.create(workshop=other, name="Secret inches", abbreviation="in")
    result = get_libraries_catalogue(actor, family="unit_type", search="met")
    assert [row["label"] for row in result["families"][0]["rows"]] == ["Metres"]
    assert "id" in result["families"][0]["rows"][0]


def test_operator_receives_no_query_envelope():
    _, workshop = library_admin("operator")
    role = workshop.roles.create(name="Operator")
    operator = User.objects.create_user(
        email="operator+query@example.test",
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
    workshop.status = Workshop.Status.OPERATIONAL
    workshop.save(update_fields=["status"])
    assert get_libraries_catalogue(operator) is None
