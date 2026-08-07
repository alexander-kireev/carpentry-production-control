from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction

from tests.test_material_commands import material_dependencies
from workshops.models import (
    Material,
    MaterialCommandReceipt,
    MaterialVariant,
    StockEffect,
)

pytestmark = pytest.mark.django_db(transaction=True)


def source_rows(suffix="schema"):
    actor, workshop, category, unit = material_dependencies(suffix)
    material = Material.objects.create(
        workshop=workshop, name=f"Material {suffix}", category=category, unit=unit
    )
    variant = MaterialVariant.objects.create(
        workshop=workshop,
        material=material,
        spec_label="Standard",
        min_threshold=Decimal("1"),
    )
    return actor, workshop, material, variant


def test_schema_has_exact_sc02_tables_indexes_and_triggers():
    assert {
        "material",
        "material_variant",
        "stock_effect",
        "material_command_receipt",
    } <= set(connection.introspection.table_names())
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename IN "
            "('material','material_variant','stock_effect','material_command_receipt')"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid IN "
            "('material'::regclass,'material_variant'::regclass,'stock_effect'::regclass,"
            "'material_command_receipt'::regclass) AND NOT tgisinternal"
        )
        triggers = {row[0] for row in cursor.fetchall()}
    assert {
        "idx_069_material_scope",
        "idx_072_variant_material",
        "idx_074_effect_variant_time",
        "idx_075_effect_source",
        "idx_076_effect_correlation",
        "idx_078_material_receipt",
    } <= indexes
    assert {
        "cst_317_318_material_guard",
        "cst_323_material_variant_scope",
        "cst_334_349_stock_effect_guard",
        "cst_351_stock_projection_sync",
        "cst_367_material_receipt_immutable",
    } <= triggers


def test_opening_effect_trigger_owns_projection_even_at_zero():
    actor, workshop, _, variant = source_rows("projection")
    effect = StockEffect.objects.create(
        workshop=workshop,
        material_variant=variant,
        effect_type="opening_balance",
        source_type="material_variant_creation",
        command_identity="opening",
        correlation_identity="scope:opening",
        actor_or_system=actor,
        delta=0,
        balance_before=0,
        balance_after=0,
        stock_projection_version=2,
    )
    variant.refresh_from_db()
    assert (variant.current_stock, variant.version) == (Decimal("0"), 2)
    with pytest.raises(DatabaseError), transaction.atomic():
        StockEffect.objects.filter(pk=effect.id).update(delta=1, balance_after=1)
    with pytest.raises(DatabaseError), transaction.atomic():
        effect.delete()


def test_projection_conflict_and_cross_tenant_effect_roll_back():
    actor, workshop, _, variant = source_rows("conflict")
    _, foreign_workshop, _, _ = source_rows("foreign")
    common = dict(
        material_variant=variant,
        effect_type="opening_balance",
        source_type="material_variant_creation",
        command_identity="conflict",
        correlation_identity="scope:conflict",
        actor_or_system=actor,
        delta=1,
        balance_after=1,
        stock_projection_version=2,
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        StockEffect.objects.create(workshop=workshop, balance_before=5, **common)
    with pytest.raises(DatabaseError), transaction.atomic():
        StockEffect.objects.create(
            workshop=foreign_workshop, balance_before=0, **common
        )
    assert StockEffect.objects.count() == 0
    variant.refresh_from_db()
    assert (variant.current_stock, variant.version) == (Decimal("0"), 1)


def test_cross_tenant_stock_effect_actor_is_rejected():
    _, workshop, _, variant = source_rows("actor-source")
    foreign_actor, _, _, _ = material_dependencies("actor-foreign")
    with pytest.raises(DatabaseError), transaction.atomic():
        StockEffect.objects.create(
            workshop=workshop,
            material_variant=variant,
            effect_type="opening_balance",
            source_type="material_variant_creation",
            command_identity="actor-scope",
            correlation_identity="scope:actor",
            actor_or_system=foreign_actor,
            delta=0,
            balance_before=0,
            balance_after=0,
            stock_projection_version=2,
        )
    assert StockEffect.objects.count() == 0


def test_material_receipt_is_immutable():
    actor, workshop, material, _ = source_rows("receipt")
    receipt = MaterialCommandReceipt.objects.create(
        workshop=workshop,
        actor_user=actor,
        target_type="material",
        target_id=material.id,
        idempotency_key="key",
        command_family="edit",
        request_fingerprint="f" * 64,
        result_version=1,
        result_summary={"material_id": material.id, "material_version": 1},
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        MaterialCommandReceipt.objects.filter(pk=receipt.id).update(result_version=2)
    with pytest.raises(DatabaseError), transaction.atomic():
        receipt.delete()
