from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from events.models import Event
from tests.test_library_commands import library_admin
from workshops.commands import create_library_item
from workshops.models import ConfigurationCommandReceipt, UnitType

pytestmark = pytest.mark.django_db(transaction=True)


def test_same_key_concurrent_create_has_one_source_receipt_and_event():
    actor, workshop = library_admin("race")

    def attempt():
        close_old_connections()
        try:
            return create_library_item(
                actor_id=actor.id,
                workshop_id=workshop.id,
                family="unit_type",
                submission_key="race-key",
                data={"name": "Metres", "abbreviation": "m"},
            ).code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(codes) == ["replay", "success"]
    assert UnitType.objects.filter(workshop=workshop, name="Metres").count() == 1
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 1
    assert Event.objects.filter(primary_subject_type="unit_type").count() == 1


def test_same_key_concurrent_changed_payload_is_fail_closed():
    actor, workshop = library_admin("race-changed")

    def attempt(data):
        close_old_connections()
        try:
            return create_library_item(
                actor_id=actor.id,
                workshop_id=workshop.id,
                family="unit_type",
                submission_key="race-changed-key",
                data=data,
            ).code
        finally:
            close_old_connections()

    payloads = (
        {"name": "Metres", "abbreviation": "m"},
        {"name": "Millimetres", "abbreviation": "mm"},
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(attempt, payloads))
    assert sorted(codes) == ["success", "unavailable"]
    assert UnitType.objects.filter(workshop=workshop).count() == 1
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 1
    assert Event.objects.filter(primary_subject_type="unit_type").count() == 1
