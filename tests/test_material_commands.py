from decimal import Decimal

import pytest
from django.db import connection

from events.models import Event, EventNotificationIntent, EventSubject
from tests.test_library_commands import library_admin
from workshops.commands import (
    create_material,
    create_material_variant,
    edit_material,
    edit_material_variant,
    transition_material,
    transition_material_variant,
)
from workshops.models import (
    ConfigurationCommandReceipt,
    Material,
    MaterialCategory,
    MaterialCommandReceipt,
    MaterialVariant,
    StockEffect,
    UnitType,
)

pytestmark = pytest.mark.django_db(transaction=True)


def material_dependencies(suffix="one"):
    actor, workshop = library_admin(f"material-{suffix}")
    category = MaterialCategory.objects.create(
        workshop=workshop, name=f"Sheet goods {suffix}"
    )
    unit = UnitType.objects.create(
        workshop=workshop, name=f"Metres {suffix}", abbreviation=f"m{suffix}"
    )
    return actor, workshop, category, unit


def material_data(category, unit, **extra):
    return {
        "name": "Birch plywood",
        "category_id": category.id,
        "category_version": category.version,
        "unit_id": unit.id,
        "unit_version": unit.version,
        **extra,
    }


@pytest.mark.parametrize("opening", ("0", "12.5000"))
def test_combined_create_records_exact_opening_and_recovers(opening):
    actor, workshop, category, unit = material_dependencies(opening.replace(".", "-"))
    data = material_data(
        category,
        unit,
        spec_label="18 mm",
        opening_quantity=opening,
        min_threshold="2.0000",
    )
    first = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="combined-key",
        data=data,
    )
    replay = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="combined-key",
        data=data,
    )
    assert (first.code, replay.code) == ("committed", "recovered")
    variant = MaterialVariant.objects.get(pk=first.variant_id)
    effect = StockEffect.objects.get(pk=first.opening_effect_id)
    assert (variant.version, variant.current_stock) == (2, Decimal(opening))
    assert (effect.balance_before, effect.delta, effect.balance_after) == (
        Decimal("0"),
        Decimal(opening),
        Decimal(opening),
    )
    assert effect.stock_projection_version == 2
    assert Material.objects.filter(workshop=workshop).count() == 1
    assert MaterialVariant.objects.filter(workshop=workshop).count() == 1
    assert StockEffect.objects.filter(workshop=workshop).count() == 1
    assert (
        ConfigurationCommandReceipt.objects.filter(
            workshop=workshop, command_type="material_create"
        ).count()
        == 1
    )
    assert Event.objects.filter(event_type="MATERIAL_CREATED").count() == 1
    assert Event.objects.filter(event_type="MATERIAL_VARIANT_CREATED").count() == 1
    assert Event.objects.filter(event_type="MATERIAL_STOCK_REPLENISHED").count() == (
        1 if Decimal(opening) > 0 else 0
    )
    assert Event.objects.count() == EventNotificationIntent.objects.count()
    assert EventSubject.objects.count() == (4 if Decimal(opening) > 0 else 3)


def test_bare_and_standalone_create_shapes_and_changed_payload_misuse():
    actor, workshop, category, unit = material_dependencies("standalone")
    bare = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="material-key",
        data=material_data(category, unit),
    )
    assert bare.code == "committed" and bare.variant_id is None
    variant_data = {
        "material_version": bare.material_version,
        "spec_label": "A grade",
        "opening_quantity": "3",
        "min_threshold": "1",
    }
    created = create_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=bare.material_id,
        submission_key="variant-key",
        data=variant_data,
    )
    misuse = create_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=bare.material_id,
        submission_key="variant-key",
        data=variant_data | {"spec_label": "Changed"},
    )
    assert (created.code, misuse.code) == ("committed", "unavailable")
    assert Material.objects.count() == 1
    assert MaterialVariant.objects.count() == 1
    assert StockEffect.objects.count() == 1


def test_partial_first_variant_and_stale_dependencies_write_nothing():
    actor, workshop, category, unit = material_dependencies("reject")
    before = {
        Material: Material.objects.count(),
        MaterialVariant: MaterialVariant.objects.count(),
        StockEffect: StockEffect.objects.count(),
        Event: Event.objects.count(),
        EventSubject: EventSubject.objects.count(),
        EventNotificationIntent: EventNotificationIntent.objects.count(),
        ConfigurationCommandReceipt: ConfigurationCommandReceipt.objects.count(),
    }
    partial = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="partial",
        data=material_data(category, unit, spec_label="Only label"),
    )
    stale = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="stale",
        data=material_data(category, unit) | {"unit_version": unit.version + 1},
    )
    assert (partial.code, stale.code) == ("invalid", "invalid")
    assert {model: model.objects.count() for model in before} == before


def test_edits_and_lifecycle_use_immutable_noncreate_receipts():
    actor, workshop, category, unit = material_dependencies("lifecycle")
    created = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="create",
        data=material_data(category, unit),
    )
    edited = edit_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=created.material_id,
        expected_version=1,
        idempotency_key="edit-material",
        data=material_data(category, unit) | {"name": "Oak"},
    )
    replay = edit_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=created.material_id,
        expected_version=1,
        idempotency_key="edit-material",
        data=material_data(category, unit) | {"name": "Oak"},
    )
    assert (edited.code, replay.code) == ("committed", "recovered")
    archived = transition_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=created.material_id,
        expected_version=2,
        idempotency_key="archive-material",
        action="archive",
    )
    assert archived.code == "committed"
    assert MaterialCommandReceipt.objects.count() == 2


def test_variant_edit_archive_restore_and_parent_blocker():
    actor, workshop, category, unit = material_dependencies("variant-life")
    material = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="material",
        data=material_data(category, unit),
    )
    variant = create_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=material.material_id,
        submission_key="variant",
        data={
            "material_version": 1,
            "spec_label": "Rough",
            "opening_quantity": "0",
            "min_threshold": "1",
        },
    )
    edited = edit_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        variant_id=variant.variant_id,
        expected_version=2,
        idempotency_key="edit-variant",
        data={"spec_label": "Planed", "min_threshold": "2"},
    )
    blocked = transition_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=material.material_id,
        expected_version=1,
        idempotency_key="blocked-material",
        action="archive",
    )
    retired = transition_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        variant_id=variant.variant_id,
        expected_version=3,
        idempotency_key="archive-variant",
        action="archive",
    )
    assert (edited.code, blocked.code, retired.code) == (
        "committed",
        "blocked",
        "committed",
    )
    assert MaterialCommandReceipt.objects.count() == 2


def _durable_counts():
    return {
        Material: Material.objects.count(),
        MaterialVariant: MaterialVariant.objects.count(),
        StockEffect: StockEffect.objects.count(),
        ConfigurationCommandReceipt: ConfigurationCommandReceipt.objects.count(),
        MaterialCommandReceipt: MaterialCommandReceipt.objects.count(),
        Event: Event.objects.count(),
        EventSubject: EventSubject.objects.count(),
        EventNotificationIntent: EventNotificationIntent.objects.count(),
    }


def _corrupt_effect(effect_id, assignment, parameters):
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stock_effect DISABLE TRIGGER cst_334_349_stock_effect_guard"
        )
        try:
            cursor.execute(
                f"UPDATE stock_effect SET {assignment} WHERE id=%s",
                (*parameters, effect_id),
            )
        finally:
            cursor.execute(
                "ALTER TABLE stock_effect ENABLE TRIGGER cst_334_349_stock_effect_guard"
            )


@pytest.mark.parametrize(
    ("assignment", "value_factory"),
    (
        ("command_identity=%s", lambda context: ("wrong-command",)),
        ("correlation_identity=%s", lambda context: ("wrong-correlation",)),
        ("source_identity=%s", lambda context: (99,)),
        ("source_version=%s", lambda context: (1,)),
        ("category=%s", lambda context: ("corrupt",)),
        ("stock_projection_version=%s", lambda context: (3,)),
        ("actor_or_system_id=%s", lambda context: (context["foreign_actor"].id,)),
        ("workshop_id=%s", lambda context: (context["foreign_workshop"].id,)),
        ("material_variant_id=%s", lambda context: (context["other_variant"].id,)),
        (
            "effect_type=%s, source_type=%s",
            lambda context: ("purchase_order_arrival", "purchase_order_arrival"),
        ),
        (
            "delta=%s, balance_after=%s",
            lambda context: (Decimal("2"), Decimal("2")),
        ),
        (
            "balance_before=%s, balance_after=%s",
            lambda context: (Decimal("1"), Decimal("2")),
        ),
    ),
)
def test_standalone_recovery_rejects_each_corrupt_opening_shape(
    assignment, value_factory
):
    actor, workshop, category, unit = material_dependencies(
        f"receipt-shape-{abs(hash(assignment))}"
    )
    foreign_actor, foreign_workshop, _, _ = material_dependencies(
        f"receipt-foreign-{abs(hash(assignment))}"
    )
    material = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="material",
        data=material_data(category, unit),
    )
    request_data = {
        "material_version": 1,
        "spec_label": "Standard",
        "opening_quantity": "1",
        "min_threshold": "1",
    }
    created = create_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=material.material_id,
        submission_key="variant-key",
        data=request_data,
    )
    other_variant = MaterialVariant.objects.create(
        workshop=workshop,
        material_id=material.material_id,
        spec_label="Other",
        min_threshold=0,
    )
    context = {
        "foreign_actor": foreign_actor,
        "foreign_workshop": foreign_workshop,
        "other_variant": other_variant,
    }
    _corrupt_effect(created.opening_effect_id, assignment, value_factory(context))
    before = _durable_counts()
    replay = create_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=material.material_id,
        submission_key="variant-key",
        data=request_data,
    )
    assert replay.code == "unavailable"
    assert _durable_counts() == before


def test_combined_recovery_rejects_wrong_command_identity_and_writes_nothing():
    actor, workshop, category, unit = material_dependencies("combined-corrupt")
    data = material_data(
        category,
        unit,
        spec_label="18 mm",
        opening_quantity="0",
        min_threshold="1",
    )
    created = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="combined",
        data=data,
    )
    _corrupt_effect(created.opening_effect_id, "command_identity=%s", ("wrong",))
    before = _durable_counts()
    replay = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="combined",
        data=data,
    )
    assert replay.code == "unavailable"
    assert _durable_counts() == before


def test_combined_recovery_rejects_missing_opening_effect_and_writes_nothing():
    actor, workshop, category, unit = material_dependencies("combined-missing")
    data = material_data(
        category,
        unit,
        spec_label="18 mm",
        opening_quantity="0",
        min_threshold="1",
    )
    created = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="combined",
        data=data,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stock_effect DISABLE TRIGGER cst_334_349_stock_effect_guard"
        )
        try:
            cursor.execute(
                "DELETE FROM stock_effect WHERE id=%s", (created.opening_effect_id,)
            )
        finally:
            cursor.execute(
                "ALTER TABLE stock_effect ENABLE TRIGGER cst_334_349_stock_effect_guard"
            )
    before = _durable_counts()
    replay = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="combined",
        data=data,
    )
    assert replay.code == "unavailable"
    assert _durable_counts() == before


def test_standalone_recovery_rejects_extra_correlated_effect_and_writes_nothing():
    actor, workshop, category, unit = material_dependencies("standalone-extra")
    material = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="material",
        data=material_data(category, unit),
    )
    request_data = {
        "material_version": 1,
        "spec_label": "Standard",
        "opening_quantity": "1",
        "min_threshold": "1",
    }
    create_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=material.material_id,
        submission_key="variant-key",
        data=request_data,
    )
    extra_variant = MaterialVariant.objects.create(
        workshop=workshop,
        material_id=material.material_id,
        spec_label="Injected duplicate",
        min_threshold=0,
    )
    StockEffect.objects.create(
        workshop=workshop,
        material_variant=extra_variant,
        effect_type="opening_balance",
        source_type="material_variant_creation",
        command_identity="variant-key",
        correlation_identity=(
            f"workshop:{workshop.id}:material_variant_create:variant-key"
        ),
        actor_or_system=actor,
        delta=0,
        balance_before=0,
        balance_after=0,
        stock_projection_version=2,
    )
    before = _durable_counts()
    replay = create_material_variant(
        actor_id=actor.id,
        workshop_id=workshop.id,
        material_id=material.material_id,
        submission_key="variant-key",
        data=request_data,
    )
    assert replay.code == "unavailable"
    assert _durable_counts() == before


def test_bare_recovery_rejects_an_extra_effect_for_its_create_correlation():
    actor, workshop, category, unit = material_dependencies("bare-extra")
    data = material_data(category, unit)
    created = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="bare",
        data=data,
    )
    variant = MaterialVariant.objects.create(
        workshop=workshop,
        material_id=created.material_id,
        spec_label="Corrupt extra",
        min_threshold=0,
    )
    StockEffect.objects.create(
        workshop=workshop,
        material_variant=variant,
        effect_type="opening_balance",
        source_type="material_variant_creation",
        command_identity="bare",
        correlation_identity=f"workshop:{workshop.id}:material_create:bare",
        actor_or_system=actor,
        delta=0,
        balance_before=0,
        balance_after=0,
        stock_projection_version=2,
    )
    before = _durable_counts()
    replay = create_material(
        actor_id=actor.id,
        workshop_id=workshop.id,
        submission_key="bare",
        data=data,
    )
    assert replay.code == "unavailable"
    assert _durable_counts() == before
