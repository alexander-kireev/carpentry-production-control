from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from events.models import Event
from tests.test_material_commands import material_data, material_dependencies
from workshops.commands import create_material
from workshops.models import (
    ConfigurationCommandReceipt,
    Material,
    StockEffect,
    Workshop,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("changed", (False, True))
def test_concurrent_material_create_serializes_to_one_aggregate(monkeypatch, changed):
    actor, workshop, category, unit = material_dependencies(f"race-{changed}")
    barrier = Barrier(2)
    original = Workshop.objects.select_for_update

    def synchronized_select(*args, **kwargs):
        queryset = original(*args, **kwargs)
        original_filter = queryset.filter

        def synchronized_filter(*filter_args, **filter_kwargs):
            barrier.wait(timeout=10)
            return original_filter(*filter_args, **filter_kwargs)

        queryset.filter = synchronized_filter
        return queryset

    monkeypatch.setattr(Workshop.objects, "select_for_update", synchronized_select)
    base = material_data(
        category,
        unit,
        spec_label="Standard",
        opening_quantity="1",
        min_threshold="1",
    )

    def submit(index):
        close_old_connections()
        try:
            data = base if not changed or index == 0 else base | {"name": "Changed"}
            return create_material(
                actor_id=actor.id,
                workshop_id=workshop.id,
                submission_key="one-key",
                data=data,
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result()
            for future in (pool.submit(submit, 0), pool.submit(submit, 1))
        ]
    assert {result.code for result in results} == (
        {"committed", "unavailable"} if changed else {"committed", "recovered"}
    )
    assert Material.objects.filter(workshop=workshop).count() == 1
    assert StockEffect.objects.filter(workshop=workshop).count() == 1
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 1
    assert Event.objects.filter(primary_subject_type="material").count() == 1
