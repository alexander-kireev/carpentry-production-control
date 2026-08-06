from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from django.db import close_old_connections

from identity.commands import create_workshop
from identity.models import User, WorkshopCreationCommandReceipt
from workshops.models import OperationType, Workshop, WorkshopRole

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("keys", "expected_codes"),
    [
        (("same", "same"), {"success", "replay"}),
        (("first", "second"), {"success", "already_advanced"}),
    ],
)
def test_concurrent_submissions_have_one_committed_result(
    monkeypatch, keys, expected_codes
):
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
    user = User.objects.create_user(
        email="race@example.test",
        password="Valid-password-483!",
        first_name="Race",
        last_name="Creator",
        date_of_birth=date(1990, 1, 1),
        account_role="admin",
        status="active",
        onboarding_state="registered_no_workshop",
    )
    data = {
        "submission_nonce": "race",
        "expected_user_version": 1,
        "name": "Race Workshop",
        "address": "1 Race Lane",
        "contact_email": "race-workshop@example.test",
        "timezone": "Europe/London",
    }
    barrier = Barrier(2)
    original = User.objects.select_for_update

    def synchronized_select(*args, **kwargs):
        queryset = original(*args, **kwargs)
        original_get = queryset.get

        def synchronized_get(*get_args, **get_kwargs):
            barrier.wait(timeout=10)
            return original_get(*get_args, **get_kwargs)

        queryset.get = synchronized_get
        return queryset

    monkeypatch.setattr(User.objects, "select_for_update", synchronized_select)

    def submit(key):
        close_old_connections()
        try:
            return create_workshop(actor_id=user.id, data=data, idempotency_key=key)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result() for future in [pool.submit(submit, key) for key in keys]
        ]
    assert {result.code.value for result in results} == expected_codes
    assert (
        Workshop.objects.count() == WorkshopCreationCommandReceipt.objects.count() == 1
    )
    assert OperationType.objects.exclude(workshop__isnull=True).count() == 2
