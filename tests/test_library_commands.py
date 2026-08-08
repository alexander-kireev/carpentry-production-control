import pytest
from django.db import connection

from events.models import Event, EventNotificationIntent, EventSubject
from events.recipient_policies import resolve_recipients
from identity.models import User
from tests.test_workshop_creation import ensure_protected_configuration
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
    Station,
    StationSupportedOperationType,
    UnitType,
    Workshop,
    WorkshopRole,
    WorkshopRoleDefaultClearance,
)
from workshops.protected_configuration import resolve_protected_configuration


def test_active_station_support_blocks_operation_type_retirement_without_writes():
    actor, workshop = library_admin("station-retirement-blocker")
    operation_type = OperationType.objects.create(
        workshop=workshop,
        name="Cutting",
        description="",
        is_production=True,
        requires_clearance=True,
    )
    station = Station.objects.create(
        workshop=workshop, code="ST-001", name="Cutting Cell"
    )
    StationSupportedOperationType.objects.create(
        station=station, operation_type=operation_type
    )
    before_events = Event.objects.count()
    blocked = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="operation_type",
        item_id=operation_type.id,
        expected_version=operation_type.version,
        action="retire",
    )
    operation_type.refresh_from_db()
    assert blocked.code == "unavailable"
    assert operation_type.status == OperationType.Status.ACTIVE
    assert Event.objects.count() == before_events


pytestmark = pytest.mark.django_db(transaction=True)


def library_admin(suffix="one"):
    ensure_protected_configuration()
    protected = resolve_protected_configuration()
    workshop = Workshop.objects.create(
        name=f"Workshop {suffix}",
        address="1 Joinery Lane",
        email=f"{suffix}@example.test",
        timezone="Europe/London",
        status=Workshop.Status.MANAGER_ACTIVATION_PENDING,
    )
    OperationType.objects.create(
        workshop=workshop,
        name="Build Planning",
        is_production=False,
        requires_clearance=True,
        machine_key="build_planning",
    )
    OperationType.objects.create(
        workshop=workshop,
        name="Station Maintenance",
        is_production=False,
        requires_clearance=True,
        machine_key="station_maintenance",
    )
    user = User.objects.create_user(
        email=f"admin+{suffix}@example.test",
        password="test-only-password",
        first_name="Ada",
        last_name="Admin",
        date_of_birth="1990-04-17",
        account_role=User.AccountRole.ADMIN,
        workshop=workshop,
        workshop_role=protected.admin_role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    return user, workshop


@pytest.mark.parametrize(
    ("family", "data", "model"),
    [
        (
            "workshop_role",
            {
                "name": "Finisher",
                "description": "Finishing",
                "default_clearance_ids": [],
                "default_clearance_versions": {},
            },
            WorkshopRole,
        ),
        (
            "operation_type",
            {
                "name": "Sanding",
                "description": "",
                "is_production": True,
                "requires_clearance": True,
            },
            OperationType,
        ),
        ("unit_type", {"name": "Metres", "abbreviation": "m"}, UnitType),
        ("material_category", {"name": "Sheet goods"}, MaterialCategory),
        (
            "shift_definition",
            {
                "name": "Early",
                "start_time": "06:00",
                "end_time": "14:00",
                "days": [0, 1, 2, 3, 4],
            },
            ShiftDefinition,
        ),
    ],
)
def test_every_family_create_is_atomic_and_replays(family, data, model):
    actor, workshop = library_admin(family)
    first = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family=family,
        submission_key="same-key",
        data=data,
    )
    replay = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family=family,
        submission_key="same-key",
        data=data,
    )
    assert (first.code, replay.code) == ("success", "replay")
    assert model.objects.filter(pk=first.result_id, workshop=workshop).count() == 1
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 1
    assert (
        Event.objects.filter(
            primary_subject_type=family, primary_subject_id=first.result_id
        ).count()
        == 1
    )
    assert EventNotificationIntent.objects.count() == 1
    expected_subjects = 0 if family == "shift_definition" else 1
    assert EventSubject.objects.count() == expected_subjects


def test_changed_payload_and_other_actor_do_not_recover_receipt():
    actor, workshop = library_admin("misuse")
    data = {"name": "Metres", "abbreviation": "m"}
    assert (
        create_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="unit_type",
            submission_key="key",
            data=data,
        ).code
        == "success"
    )
    assert (
        create_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="unit_type",
            submission_key="key",
            data=data | {"name": "Millimetres"},
        ).code
        == "unavailable"
    )


def test_edit_stale_and_lifecycle_event_silence():
    actor, workshop = library_admin("lifecycle")
    created = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="key",
        data={"name": "Metres", "abbreviation": "m"},
    )
    edited = edit_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        item_id=created.result_id,
        expected_version=1,
        data={"name": "Linear metres"},
    )
    stale = edit_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        item_id=created.result_id,
        expected_version=1,
        data={"name": "Wrong"},
    )
    retired = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        item_id=created.result_id,
        expected_version=2,
        action="retire",
    )
    noop = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        item_id=created.result_id,
        expected_version=3,
        action="retire",
    )
    assert (edited.code, stale.code, retired.code, noop.code) == (
        "success",
        "stale",
        "success",
        "success",
    )
    assert (
        Event.objects.filter(
            primary_subject_id=created.result_id, primary_subject_type="unit_type"
        ).count()
        == 3
    )


def test_default_clearance_accepts_exact_global_other_and_marks_first_reference():
    actor, workshop = library_admin("other")
    other = resolve_protected_configuration().other_operation_type
    result = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        submission_key="key",
        data={
            "name": "Flexible",
            "description": "",
            "default_clearance_ids": [other.id],
            "default_clearance_versions": {other.id: other.version},
        },
    )
    assert result.code == "success"
    other.refresh_from_db()
    assert other.first_referenced_at is not None


def _workshop_role_effect_counts(workshop):
    return (
        WorkshopRole.objects.filter(workshop=workshop).count(),
        WorkshopRoleDefaultClearance.objects.filter(
            workshop_role__workshop=workshop
        ).count(),
        ConfigurationCommandReceipt.objects.filter(workshop=workshop).count(),
        Event.objects.filter(primary_subject_type="workshop_role").count(),
        EventSubject.objects.filter(
            subject_type="workshop", subject_id=workshop.id
        ).count(),
        EventNotificationIntent.objects.filter(
            event__primary_subject_type="workshop_role"
        ).count(),
    )


def test_workshop_role_create_requires_exact_current_clearance_version_evidence():
    actor, workshop = library_admin("clearance-create-evidence")
    first = OperationType.objects.create(
        workshop=workshop,
        name="Cutting",
        is_production=True,
        requires_clearance=True,
    )
    second = OperationType.objects.create(
        workshop=workshop,
        name="Sanding",
        is_production=True,
        requires_clearance=True,
    )
    rejected = (
        {
            "default_clearance_ids": [first.id],
        },
        {
            "default_clearance_ids": [first.id, second.id],
            "default_clearance_versions": {first.id: first.version},
        },
        {
            "default_clearance_ids": [first.id],
            "default_clearance_versions": {
                first.id: first.version,
                second.id: second.version,
            },
        },
        {
            "default_clearance_ids": [first.id],
            "default_clearance_versions": {first.id: "malformed"},
        },
        {
            "default_clearance_ids": [first.id],
            "default_clearance_versions": {first.id: first.version + 1},
        },
    )
    baseline = _workshop_role_effect_counts(workshop)
    for index, evidence in enumerate(rejected):
        result = create_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="workshop_role",
            submission_key=f"rejected-{index}",
            data={"name": f"Rejected {index}", "description": ""} | evidence,
        )
        assert (result.code, result.result_id, result.version) == (
            "validation_error",
            None,
            None,
        )
        assert _workshop_role_effect_counts(workshop) == baseline
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.first_referenced_at is second.first_referenced_at is None

    current = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        submission_key="current",
        data={
            "name": "Current",
            "description": "",
            "default_clearance_ids": [str(first.id)],
            "default_clearance_versions": {str(first.id): str(first.version)},
        },
    )
    assert current.code == "success"
    assert _workshop_role_effect_counts(workshop) == (1, 1, 1, 1, 1, 1)

    captured_version = second.version
    OperationType.objects.filter(pk=second.id).update(version=second.version + 1)
    competing_baseline = _workshop_role_effect_counts(workshop)
    competing = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        submission_key="competing",
        data={
            "name": "Competing",
            "description": "",
            "default_clearance_ids": [second.id],
            "default_clearance_versions": {second.id: captured_version},
        },
    )
    assert (competing.code, competing.result_id) == ("validation_error", None)
    assert _workshop_role_effect_counts(workshop) == competing_baseline
    second.refresh_from_db()
    assert second.first_referenced_at is None


def test_workshop_role_edit_requires_exact_current_clearance_version_evidence():
    actor, workshop = library_admin("clearance-edit-evidence")
    first = OperationType.objects.create(
        workshop=workshop,
        name="Cutting",
        is_production=True,
        requires_clearance=True,
    )
    second = OperationType.objects.create(
        workshop=workshop,
        name="Sanding",
        is_production=True,
        requires_clearance=True,
    )
    created = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        submission_key="source",
        data={
            "name": "Finisher",
            "description": "",
            "default_clearance_ids": [],
            "default_clearance_versions": {},
        },
    )
    source = WorkshopRole.objects.get(pk=created.result_id)
    rejected = (
        {"default_clearance_ids": [first.id]},
        {
            "default_clearance_ids": [first.id, second.id],
            "default_clearance_versions": {first.id: first.version},
        },
        {
            "default_clearance_ids": [first.id],
            "default_clearance_versions": {
                first.id: first.version,
                second.id: second.version,
            },
        },
        {
            "default_clearance_ids": [first.id],
            "default_clearance_versions": {first.id: 0},
        },
        {
            "default_clearance_ids": [first.id],
            "default_clearance_versions": {first.id: first.version + 1},
        },
    )
    baseline = _workshop_role_effect_counts(workshop)
    for evidence in rejected:
        result = edit_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="workshop_role",
            item_id=source.id,
            expected_version=1,
            data=evidence,
        )
        assert (result.code, result.result_id, result.version) == (
            "validation_error",
            None,
            None,
        )
        source.refresh_from_db()
        assert (source.name, source.version) == ("Finisher", 1)
        assert _workshop_role_effect_counts(workshop) == baseline
        assert source.default_clearance_links.count() == 0
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.first_referenced_at is second.first_referenced_at is None

    current = edit_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        item_id=source.id,
        expected_version=1,
        data={
            "default_clearance_ids": [first.id],
            "default_clearance_versions": {first.id: first.version},
        },
    )
    assert (current.code, current.version) == ("success", 2)
    assert _workshop_role_effect_counts(workshop) == (1, 1, 1, 2, 2, 2)

    captured_version = second.version
    OperationType.objects.filter(pk=second.id).update(version=second.version + 1)
    competing_baseline = _workshop_role_effect_counts(workshop)
    competing = edit_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        item_id=source.id,
        expected_version=2,
        data={
            "default_clearance_ids": [second.id],
            "default_clearance_versions": {second.id: captured_version},
        },
    )
    assert (competing.code, competing.result_id) == ("validation_error", None)
    source.refresh_from_db()
    assert source.version == 2
    assert set(
        source.default_clearance_links.values_list("operation_type_id", flat=True)
    ) == {first.id}
    assert _workshop_role_effect_counts(workshop) == competing_baseline
    second.refresh_from_db()
    assert second.first_referenced_at is None


@pytest.mark.parametrize(
    ("family", "data"),
    [
        (
            "workshop_role",
            {"name": "  ", "description": "", "default_clearance_ids": []},
        ),
        (
            "operation_type",
            {
                "name": "Cut",
                "description": "",
                "is_production": "yes",
                "requires_clearance": False,
            },
        ),
        ("unit_type", {"name": "Length", "abbreviation": "  "}),
        ("material_category", {"name": "\t"}),
        (
            "shift_definition",
            {"name": "Late", "start_time": "16:00", "end_time": "08:00", "days": [0]},
        ),
    ],
)
def test_normalized_nonblank_validation_has_zero_effects(family, data):
    actor, workshop = library_admin(f"invalid-{family}")
    before = (
        Event.objects.count(),
        EventSubject.objects.count(),
        EventNotificationIntent.objects.count(),
    )
    result = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family=family,
        submission_key="invalid",
        data=data,
    )
    assert result.code == "validation_error"
    assert result.result_id is None
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 0
    assert (
        Event.objects.count(),
        EventSubject.objects.count(),
        EventNotificationIntent.objects.count(),
    ) == before


def test_invalid_edit_has_zero_event_and_intent_effects():
    actor, workshop = library_admin("invalid-edit")
    source = UnitType.objects.create(workshop=workshop, name="Metres", abbreviation="m")
    before = (
        Event.objects.count(),
        EventSubject.objects.count(),
        EventNotificationIntent.objects.count(),
    )
    result = edit_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        item_id=source.id,
        expected_version=1,
        data={"name": "  ", "abbreviation": ""},
    )
    source.refresh_from_db()
    assert (result.code, result.result_id) == ("validation_error", None)
    assert (source.name, source.abbreviation, source.version) == ("Metres", "m", 1)
    assert (
        Event.objects.count(),
        EventSubject.objects.count(),
        EventNotificationIntent.objects.count(),
    ) == before


def test_replay_reauthorizes_permission_actor_and_safe_result():
    actor, workshop = library_admin("replay-authority")
    data = {"name": "Metres", "abbreviation": "m"}
    first = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="key",
        data=data,
    )
    assert first.code == "success"
    receipt = ConfigurationCommandReceipt.objects.get(workshop=workshop)
    baseline = (
        UnitType.objects.filter(workshop=workshop).count(),
        Event.objects.count(),
        ConfigurationCommandReceipt.objects.count(),
    )

    other_actor, _ = library_admin("replay-other")
    denied = create_library_item(
        actor_id=other_actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="key",
        data=data,
    )
    assert (denied.code, denied.result_id) == ("unavailable", None)
    User.objects.filter(pk=actor.id).update(status=User.Status.PENDING)
    denied = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="key",
        data=data,
    )
    assert (denied.code, denied.result_id) == ("unavailable", None)
    User.objects.filter(pk=actor.id).update(status=User.Status.ACTIVE)

    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE configuration_command_receipt DISABLE TRIGGER cst_sc01_receipt_guard"
        )
        try:
            cursor.execute(
                "UPDATE configuration_command_receipt SET result_id = %s WHERE id = %s",
                [987654321, receipt.id],
            )
        finally:
            cursor.execute(
                "ALTER TABLE configuration_command_receipt ENABLE TRIGGER cst_sc01_receipt_guard"
            )
    denied = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="key",
        data=data,
    )
    assert (denied.code, denied.result_id) == ("unavailable", None)
    assert (
        UnitType.objects.filter(workshop=workshop).count(),
        Event.objects.count(),
        ConfigurationCommandReceipt.objects.count(),
    ) == baseline


@pytest.mark.parametrize(
    "fault_target",
    [
        "workshops.commands.WorkshopRoleDefaultClearance.objects.create",
        "workshops.commands.produce_events",
        "events.producer.EventNotificationIntent.objects.create",
        "events.producer.EventSubject.objects.bulk_create",
        "workshops.commands.ConfigurationCommandReceipt.objects.create",
    ],
)
def test_late_faults_roll_back_child_event_subject_intent_and_receipt(
    monkeypatch, fault_target
):
    actor, workshop = library_admin(f"fault-{fault_target.split('.')[1]}")
    other = resolve_protected_configuration().other_operation_type
    data = {
        "name": "Fault role",
        "description": "",
        "default_clearance_ids": [other.id],
        "default_clearance_versions": {other.id: other.version},
    }

    def fail(*args, **kwargs):
        raise RuntimeError("injected late failure")

    monkeypatch.setattr(fault_target, fail)
    with pytest.raises(RuntimeError, match="injected"):
        create_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="workshop_role",
            submission_key="fault",
            data=data,
        )
    assert (
        WorkshopRole.objects.filter(workshop=workshop, name="Fault role").count() == 0
    )
    assert (
        WorkshopRoleDefaultClearance.objects.filter(
            workshop_role__workshop=workshop
        ).count()
        == 0
    )
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 0
    assert Event.objects.filter(primary_subject_type="workshop_role").count() == 0
    assert EventSubject.objects.count() == 0
    assert EventNotificationIntent.objects.count() == 0


def test_replay_rejects_wrong_tenant_result_without_disclosure():
    actor, workshop = library_admin("wrong-result")
    _, foreign_workshop = library_admin("wrong-result-foreign")
    data = {"name": "Metres", "abbreviation": "m"}
    first = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="key",
        data=data,
    )
    foreign = UnitType.objects.create(
        workshop=foreign_workshop, name="Foreign", abbreviation="f"
    )
    receipt = ConfigurationCommandReceipt.objects.get(workshop=workshop)
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE configuration_command_receipt DISABLE TRIGGER cst_sc01_receipt_guard"
        )
        try:
            cursor.execute(
                "UPDATE configuration_command_receipt SET result_id = %s WHERE id = %s",
                [foreign.id, receipt.id],
            )
        finally:
            cursor.execute(
                "ALTER TABLE configuration_command_receipt ENABLE TRIGGER cst_sc01_receipt_guard"
            )
    result = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="key",
        data=data,
    )
    assert first.code == "success"
    assert (result.code, result.result_id, result.version) == (
        "unavailable",
        None,
        None,
    )
    assert UnitType.objects.filter(workshop=workshop).count() == 1
    assert ConfigurationCommandReceipt.objects.filter(workshop=workshop).count() == 1


@pytest.mark.parametrize(
    ("family", "create_data", "edit_data", "model"),
    [
        (
            "workshop_role",
            {
                "name": "Finisher",
                "description": "Old",
                "default_clearance_ids": [],
                "default_clearance_versions": {},
            },
            {
                "name": "Senior finisher",
                "description": "New",
                "default_clearance_ids": [],
                "default_clearance_versions": {},
            },
            WorkshopRole,
        ),
        (
            "operation_type",
            {
                "name": "Sanding",
                "description": "Old",
                "is_production": True,
                "requires_clearance": True,
            },
            {
                "name": "Fine sanding",
                "description": "New",
                "is_production": False,
                "requires_clearance": False,
            },
            OperationType,
        ),
        (
            "unit_type",
            {"name": "Metres", "abbreviation": "m"},
            {"name": "Linear metres", "abbreviation": "lm"},
            UnitType,
        ),
        (
            "material_category",
            {"name": "Sheet goods"},
            {"name": "Panels"},
            MaterialCategory,
        ),
        (
            "shift_definition",
            {
                "name": "Early",
                "start_time": "06:00",
                "end_time": "14:00",
                "days": [0, 1],
            },
            {
                "name": "Days",
                "start_time": "07:00",
                "end_time": "15:00",
                "days": [2, 3],
            },
            ShiftDefinition,
        ),
    ],
)
def test_every_family_full_edit_retire_restore_lifecycle(
    family, create_data, edit_data, model
):
    actor, workshop = library_admin(f"full-{family}")
    created = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family=family,
        submission_key="key",
        data=create_data,
    )
    edited = edit_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family=family,
        item_id=created.result_id,
        expected_version=1,
        data=edit_data,
    )
    retired = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family=family,
        item_id=created.result_id,
        expected_version=2,
        action="retire",
    )
    restored = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family=family,
        item_id=created.result_id,
        expected_version=3,
        action="restore",
    )
    source = model.objects.get(pk=created.result_id)
    assert (created.code, edited.code, retired.code, restored.code) == (
        "success",
        "success",
        "success",
        "success",
    )
    assert (source.status, source.version) == ("active", 4)
    assert (
        Event.objects.filter(
            primary_subject_type=family, primary_subject_id=source.id
        ).count()
        == 4
    )


def test_retire_blockers_restore_collision_and_manager_recipients():
    actor, workshop = library_admin("blockers")
    role_result = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        submission_key="role",
        data={
            "name": "Manager",
            "description": "",
            "default_clearance_ids": [],
            "default_clearance_versions": {},
        },
    )
    role = WorkshopRole.objects.get(pk=role_result.result_id)
    manager = User.objects.create_user(
        email="manager+blockers@example.test",
        password="test-only-password",
        first_name="Mara",
        last_name="Manager",
        date_of_birth="1990-01-01",
        account_role=User.AccountRole.MANAGER,
        workshop=workshop,
        workshop_role=role,
        onboarding_state=None,
        status=User.Status.ACTIVE,
    )
    blocked = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="workshop_role",
        item_id=role.id,
        expected_version=1,
        action="retire",
    )
    assert (blocked.code, role.version) == ("unavailable", 1)

    unit = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        submission_key="unit",
        data={"name": "Metres", "abbreviation": "m"},
    )
    retired = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        item_id=unit.result_id,
        expected_version=1,
        action="retire",
    )
    restored = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="unit_type",
        item_id=unit.result_id,
        expected_version=2,
        action="restore",
    )
    assert (retired.code, restored.code) == ("success", "success")
    for event_type in ("UNIT_TYPE_RETIRED", "UNIT_TYPE_RESTORED"):
        recipients = resolve_recipients(
            Event.objects.get(
                primary_subject_type="unit_type",
                primary_subject_id=unit.result_id,
                event_type=event_type,
            )
        )
        assert [recipient.user_id for recipient in recipients] == [manager.id]

    operation = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="operation_type",
        submission_key="operation",
        data={
            "name": "Cutting",
            "description": "",
            "is_production": True,
            "requires_clearance": True,
        },
    )
    WorkshopRoleDefaultClearance.objects.create(
        workshop_role=role, operation_type_id=operation.result_id
    )
    blocked_operation = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="operation_type",
        item_id=operation.result_id,
        expected_version=1,
        action="retire",
    )
    assert blocked_operation.code == "unavailable"

    shift = create_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="shift_definition",
        submission_key="shift",
        data={"name": "Early", "start_time": "06:00", "end_time": "14:00", "days": [0]},
    )
    assert (
        transition_library_item(
            actor_id=actor.id,
            workshop_id=workshop.id,
            family="shift_definition",
            item_id=shift.result_id,
            expected_version=1,
            action="retire",
        ).code
        == "success"
    )
    ShiftDefinition.objects.create(
        workshop=workshop, name="Early", start_time="06:00", end_time="14:00", days=[1]
    )
    collision = transition_library_item(
        actor_id=actor.id,
        workshop_id=workshop.id,
        family="shift_definition",
        item_id=shift.result_id,
        expected_version=2,
        action="restore",
    )
    assert collision.code == "validation_error"
    assert ShiftDefinition.objects.get(pk=shift.result_id).status == "retired"
