"""Dependent-type imports + read-only tables (Slice C2).

Covers the Full-protocol contract for the three name-resolving imports:

- WorkshopRole ``default_clearances`` resolution, the reserved-seed guard.
- Station ``category``/``supported_operations`` resolution, the per-workshop
  ``ST-NNN`` code (CHG-061), the optional ``status`` column.
- Material + MaterialVariant grouped combined import (bare Materials, D-121;
  ``lot_sizes`` JSON; per-row validation and idempotency).

Plus the three read-only tables: rendering, grouped-variant display, empty state,
pagination (Material by Material), search (incl. spec_label), sort, the Station
category+status filter, and the Material category+unit+low-stock filter (confirming
Low stock is ``current_stock`` vs ``min_threshold`` only — reserved/available play
no part). Runs against PostgreSQL (config.settings.test).
"""

import csv
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from catalog.models import (
    Material,
    MaterialVariant,
    Station,
    WorkshopRole,
)
from catalog.seeds import ADMIN_ROLE_NAME, UNDEFINED_NAME
from catalog.services import import_library, import_materials
from tests.factories import (
    MaterialCategoryFactory,
    MaterialFactory,
    MaterialVariantFactory,
    OperationTypeFactory,
    StationCategoryFactory,
    StationFactory,
    UnitTypeFactory,
    UserFactory,
    WorkshopFactory,
    WorkshopRoleFactory,
)

pytestmark = pytest.mark.django_db

ROLE_HEADER = ["name", "description", "default_clearances"]
STATION_HEADER = ["name", "category", "supported_operations", "status"]
MATERIAL_HEADER = [
    "name",
    "category",
    "unit",
    "spec_label",
    "current_stock",
    "min_threshold",
    "lot_sizes",
]


def _write(directory, filename, header, rows):
    path = directory / filename
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


# --------------------------------------------------------------------------- #
# WorkshopRole — default_clearances resolution + reserved-seed guard
# --------------------------------------------------------------------------- #


def test_workshoprole_default_clearances_resolve(tmp_path):
    ws = WorkshopFactory()
    OperationTypeFactory(workshop=ws, name="Cutting")
    OperationTypeFactory(workshop=ws, name="Assembly")
    _write(
        tmp_path, "workshop-role.csv", ROLE_HEADER,
        [["Bench Joiner", "Joinery", "Cutting;Assembly"]],
    )

    summary = import_library("workshop-role", ws, base_dir=tmp_path)

    assert summary.imported == 1
    role = WorkshopRole.objects.get(workshop=ws, name="Bench Joiner")
    assert set(role.default_clearances.values_list("name", flat=True)) == {"Cutting", "Assembly"}


def test_workshoprole_may_have_no_clearances(tmp_path):
    ws = WorkshopFactory()
    _write(tmp_path, "workshop-role.csv", ROLE_HEADER, [["Storeperson", "Stores", ""]])

    summary = import_library("workshop-role", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert WorkshopRole.objects.get(workshop=ws, name="Storeperson").default_clearances.count() == 0


def test_workshoprole_unresolvable_clearance_skips_whole_row(tmp_path):
    ws = WorkshopFactory()
    OperationTypeFactory(workshop=ws, name="Cutting")
    _write(tmp_path, "workshop-role.csv", ROLE_HEADER, [["Bench Joiner", "", "Cutting;Ghost Op"]])

    summary = import_library("workshop-role", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "Ghost Op" in summary.skipped[0].reason
    assert "clearance" in summary.skipped[0].reason
    # Whole row skipped — the role is not created even though one clearance resolved.
    assert not WorkshopRole.objects.filter(workshop=ws, name="Bench Joiner").exists()


def test_workshoprole_reserved_names_skipped_and_sentinels_untouched(tmp_path):
    ws = WorkshopFactory()
    _write(
        tmp_path,
        "workshop-role.csv",
        ROLE_HEADER,
        [
            [ADMIN_ROLE_NAME, "", ""],
            [UNDEFINED_NAME, "", ""],
            ["ADMIN", "", ""],  # case-insensitive
            ["Custom Role", "", ""],
        ],
    )

    summary = import_library("workshop-role", ws, base_dir=tmp_path)

    assert summary.imported == 1  # only "Custom Role"
    assert all("reserved" in row.reason for row in summary.skipped)
    # No workshop-scoped duplicate of a reserved name was created.
    assert not WorkshopRole.objects.filter(
        workshop=ws, name__in=[ADMIN_ROLE_NAME, UNDEFINED_NAME, "ADMIN"]
    ).exists()
    # The global NULL-workshop sentinels are still single, untouched rows.
    assert WorkshopRole.objects.filter(workshop__isnull=True, name=ADMIN_ROLE_NAME).count() == 1
    assert WorkshopRole.objects.filter(workshop__isnull=True, name=UNDEFINED_NAME).count() == 1


def test_workshoprole_resolution_is_workshop_scoped(tmp_path):
    ws, other = WorkshopFactory(), WorkshopFactory()
    OperationTypeFactory(workshop=other, name="Cutting")  # exists only in the other workshop
    _write(tmp_path, "workshop-role.csv", ROLE_HEADER, [["Bench Joiner", "", "Cutting"]])

    summary = import_library("workshop-role", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "Cutting" in summary.skipped[0].reason


def test_workshoprole_reimport_is_idempotent(tmp_path):
    ws = WorkshopFactory()
    OperationTypeFactory(workshop=ws, name="Cutting")
    _write(tmp_path, "workshop-role.csv", ROLE_HEADER, [["Bench Joiner", "", "Cutting"]])
    import_library("workshop-role", ws, base_dir=tmp_path)

    summary = import_library("workshop-role", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert summary.skipped_count == 1
    assert WorkshopRole.objects.filter(workshop=ws, name="Bench Joiner").count() == 1


# --------------------------------------------------------------------------- #
# Station — category / supported_operations resolution, status, ST-NNN
# --------------------------------------------------------------------------- #


def test_station_resolves_category_and_operations_and_assigns_code(tmp_path):
    ws = WorkshopFactory()
    StationCategoryFactory(workshop=ws, name="Sawing", colour="#111111")
    OperationTypeFactory(workshop=ws, name="Ripping")
    OperationTypeFactory(workshop=ws, name="Cross-Cutting")
    _write(
        tmp_path, "station.csv", STATION_HEADER,
        [["Panel Saw", "Sawing", "Ripping;Cross-Cutting", ""]],
    )

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 1
    station = Station.objects.get(workshop=ws, name="Panel Saw")
    assert station.code == "ST-001"
    assert station.category.name == "Sawing"
    assert station.status == Station.Status.ACTIVE
    assert set(station.supported_operations.values_list("name", flat=True)) == {
        "Ripping",
        "Cross-Cutting",
    }


def test_station_unresolvable_category_skipped(tmp_path):
    ws = WorkshopFactory()
    OperationTypeFactory(workshop=ws, name="Ripping")
    _write(
        tmp_path, "station.csv", STATION_HEADER,
        [["Panel Saw", "Ghost Category", "Ripping", ""]],
    )

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "Ghost Category" in summary.skipped[0].reason
    assert "Station Categories" in summary.skipped[0].reason
    assert not Station.objects.filter(workshop=ws).exists()


def test_station_unresolvable_operation_skips_whole_row(tmp_path):
    ws = WorkshopFactory()
    StationCategoryFactory(workshop=ws, name="Sawing")
    OperationTypeFactory(workshop=ws, name="Ripping")
    _write(
        tmp_path, "station.csv", STATION_HEADER,
        [["Panel Saw", "Sawing", "Ripping;Ghost Op", ""]],
    )

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "operation" in summary.skipped[0].reason
    assert "Ghost Op" in summary.skipped[0].reason
    assert not Station.objects.filter(workshop=ws).exists()  # nothing partially created


def test_station_non_production_operation_skipped(tmp_path):
    ws = WorkshopFactory()
    StationCategoryFactory(workshop=ws, name="Sawing")
    OperationTypeFactory(workshop=ws, name="Ripping", is_production=True)
    OperationTypeFactory(workshop=ws, name="Build Planning", is_production=False)
    _write(
        tmp_path, "station.csv", STATION_HEADER,
        [["Panel Saw", "Sawing", "Ripping;Build Planning", ""]],
    )

    summary = import_library("station", ws, base_dir=tmp_path)

    # supported_operations is is_production=true only (operation_type/definition.md).
    # A non-production type is reported exactly like an unresolved name — the importer
    # doesn't distinguish "doesn't exist" from "exists but isn't a production type".
    assert summary.imported == 0
    reason = summary.skipped[0].reason
    assert "operation" in reason
    assert "Build Planning" in reason
    assert "Op Types" in reason
    assert not Station.objects.filter(workshop=ws).exists()  # whole row skipped


def test_station_missing_category_reported(tmp_path):
    ws = WorkshopFactory()
    _write(tmp_path, "station.csv", STATION_HEADER, [["Panel Saw", "", "", ""]])

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "missing required field" in summary.skipped[0].reason
    assert "category" in summary.skipped[0].reason


def test_station_status_defaults_active_and_accepts_valid_value(tmp_path):
    ws = WorkshopFactory()
    StationCategoryFactory(workshop=ws, name="Maintenance")
    StationCategoryFactory(workshop=ws, name="Sawing")
    _write(
        tmp_path,
        "station.csv",
        STATION_HEADER,
        [["Bench Saw", "Sawing", "", ""], ["Maint Bay", "Maintenance", "", "under_maintenance"]],
    )

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 2
    assert Station.objects.get(workshop=ws, name="Bench Saw").status == Station.Status.ACTIVE
    assert Station.objects.get(workshop=ws, name="Maint Bay").status == "under_maintenance"


def test_station_invalid_status_skipped(tmp_path):
    ws = WorkshopFactory()
    StationCategoryFactory(workshop=ws, name="Sawing")
    _write(tmp_path, "station.csv", STATION_HEADER, [["Panel Saw", "Sawing", "", "banana"]])

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "invalid status" in summary.skipped[0].reason


def test_station_empty_supported_operations_is_valid(tmp_path):
    ws = WorkshopFactory()
    StationCategoryFactory(workshop=ws, name="Storage")
    _write(tmp_path, "station.csv", STATION_HEADER, [["Timber Store", "Storage", "", ""]])

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert Station.objects.get(workshop=ws, name="Timber Store").supported_operations.count() == 0


def test_station_reimport_is_idempotent(tmp_path):
    ws = WorkshopFactory()
    StationCategoryFactory(workshop=ws, name="Sawing")
    _write(tmp_path, "station.csv", STATION_HEADER, [["Panel Saw", "Sawing", "", ""]])
    import_library("station", ws, base_dir=tmp_path)
    original_code = Station.objects.get(workshop=ws, name="Panel Saw").code

    summary = import_library("station", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert summary.skipped_count == 1
    assert Station.objects.filter(workshop=ws).count() == 1
    assert Station.objects.get(workshop=ws, name="Panel Saw").code == original_code


# --------------------------------------------------------------------------- #
# Station.code — per-workshop sequence + uniqueness (CHG-061)
# --------------------------------------------------------------------------- #


def test_station_codes_sequence_per_workshop():
    ws1, ws2 = WorkshopFactory(), WorkshopFactory()
    a = StationFactory(workshop=ws1)
    b = StationFactory(workshop=ws1)
    c = StationFactory(workshop=ws2)

    assert (a.code, b.code) == ("ST-001", "ST-002")
    assert c.code == "ST-001"  # each workshop starts its own sequence


def test_station_code_unique_per_workshop_not_globally():
    ws1, ws2 = WorkshopFactory(), WorkshopFactory()
    cat1 = StationCategoryFactory(workshop=ws1)
    cat2 = StationCategoryFactory(workshop=ws2)
    Station.objects.create(workshop=ws1, name="A", category=cat1, code="ST-001")

    # Same code in a different workshop is allowed.
    Station.objects.create(workshop=ws2, name="B", category=cat2, code="ST-001")

    # Same code within the same workshop is rejected by the constraint.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Station.objects.create(workshop=ws1, name="C", category=cat1, code="ST-001")


# --------------------------------------------------------------------------- #
# Material + MaterialVariant — grouped combined import
# --------------------------------------------------------------------------- #


def _material_prereqs(ws, *, category="Hardwood", unit="Length"):
    MaterialCategoryFactory(workshop=ws, name=category)
    UnitTypeFactory(workshop=ws, name=unit, abbreviation=unit[:3].lower())


def test_material_multi_variant_import(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws)
    _write(
        tmp_path,
        "material.csv",
        MATERIAL_HEADER,
        [
            ["Oak Board", "Hardwood", "Length", "25x100", "120", "40", "10;25"],
            ["Oak Board", "Hardwood", "Length", "25x150", "64", "30", "10"],
        ],
    )

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 2
    material = Material.objects.get(workshop=ws, name="Oak Board")
    assert material.variants.count() == 2
    variant = material.variants.get(spec_label="25x100")
    assert variant.current_stock == Decimal("120")
    assert variant.lot_sizes == [10, 25]


def test_material_bare_material_has_zero_variants(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws, category="Fillers", unit="Piece")
    _write(
        tmp_path, "material.csv", MATERIAL_HEADER,
        [["Wood Filler", "Fillers", "Piece", "", "", "", ""]],
    )

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 1
    material = Material.objects.get(workshop=ws, name="Wood Filler")
    assert material.variants.count() == 0


def test_material_later_rows_attach_variants_to_established_material(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws)
    _write(
        tmp_path,
        "material.csv",
        MATERIAL_HEADER,
        [
            ["Oak Board", "Hardwood", "Length", "A", "10", "5", "1"],
            # Material-level columns blank — the Material is already established.
            ["Oak Board", "", "", "B", "20", "5", "1"],
        ],
    )

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 2
    assert Material.objects.filter(workshop=ws, name="Oak Board").count() == 1
    assert Material.objects.get(workshop=ws, name="Oak Board").variants.count() == 2


def test_material_unresolvable_category_skipped(tmp_path):
    ws = WorkshopFactory()
    UnitTypeFactory(workshop=ws, name="Piece", abbreviation="pc")
    _write(tmp_path, "material.csv", MATERIAL_HEADER, [["X", "Ghost", "Piece", "s", "1", "1", "1"]])

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "not found in Material Categories" in summary.skipped[0].reason
    assert not Material.objects.filter(workshop=ws).exists()


def test_material_unresolvable_unit_skipped(tmp_path):
    ws = WorkshopFactory()
    MaterialCategoryFactory(workshop=ws, name="Hardwood")
    _write(
        tmp_path, "material.csv", MATERIAL_HEADER,
        [["X", "Hardwood", "Furlong", "s", "1", "1", "1"]],
    )

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "not found in Unit Types" in summary.skipped[0].reason
    assert not Material.objects.filter(workshop=ws).exists()


def test_material_variant_missing_required_field_skips_row(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws)
    # spec_label present but current_stock blank → invalid variant → whole row skipped,
    # and the Material is not created from it either.
    _write(
        tmp_path, "material.csv", MATERIAL_HEADER,
        [["Oak Board", "Hardwood", "Length", "A", "", "5", "1"]],
    )

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "current_stock" in summary.skipped[0].reason
    assert not Material.objects.filter(workshop=ws).exists()


def test_material_invalid_lot_size_skipped(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws)
    _write(
        tmp_path, "material.csv", MATERIAL_HEADER,
        [["Oak Board", "Hardwood", "Length", "A", "5", "2", "10;abc"]],
    )

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert "lot size" in summary.skipped[0].reason


def test_material_lot_sizes_stored_as_json_numbers(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws)
    _write(
        tmp_path,
        "material.csv",
        MATERIAL_HEADER,
        [["Oak Board", "Hardwood", "Length", "A", "5", "2", "10;2.5;50"]],
    )

    import_materials(ws, base_dir=tmp_path)

    variant = MaterialVariant.objects.get(material__workshop=ws, spec_label="A")
    assert variant.lot_sizes == [10, 2.5, 50]  # whole numbers stay ints; fractional stays float


def test_material_duplicate_variant_skipped(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws)
    _write(
        tmp_path,
        "material.csv",
        MATERIAL_HEADER,
        [
            ["Oak Board", "Hardwood", "Length", "A", "5", "2", "1"],
            ["Oak Board", "Hardwood", "Length", "A", "9", "2", "1"],  # same spec_label
        ],
    )

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert "duplicate variant" in summary.skipped[0].reason
    assert MaterialVariant.objects.filter(material__workshop=ws, spec_label="A").count() == 1


def test_material_reimport_is_idempotent(tmp_path):
    ws = WorkshopFactory()
    _material_prereqs(ws)
    rows = [
        ["Oak Board", "Hardwood", "Length", "A", "5", "2", "1"],
        ["Wood Filler", "Hardwood", "Length", "", "", "", ""],  # bare
    ]
    _write(tmp_path, "material.csv", MATERIAL_HEADER, rows)
    import_materials(ws, base_dir=tmp_path)

    summary = import_materials(ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert summary.skipped_count == 2
    assert Material.objects.filter(workshop=ws).count() == 2
    assert MaterialVariant.objects.filter(material__workshop=ws).count() == 1


# --------------------------------------------------------------------------- #
# Bundled fixtures import cleanly (with prerequisites imported first)
# --------------------------------------------------------------------------- #


def test_bundled_station_library_imports_cleanly():
    ws = WorkshopFactory()
    import_library("station-category", ws)
    import_library("operation-type", ws)

    summary = import_library("station", ws)

    assert summary.error is None
    assert summary.skipped == []
    assert Station.objects.filter(workshop=ws).count() == summary.imported > 0


def test_bundled_material_library_imports_cleanly():
    ws = WorkshopFactory()
    import_library("material-category", ws)
    import_library("unit-type", ws)

    summary = import_materials(ws)

    assert summary.error is None
    assert summary.skipped == []
    assert Material.objects.filter(workshop=ws).exists()
    assert MaterialVariant.objects.filter(material__workshop=ws).exists()


# --------------------------------------------------------------------------- #
# Import views — permission boundary + population
# --------------------------------------------------------------------------- #


def test_station_import_requires_login(client):
    response = client.post(reverse("catalog:station_import"))
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_material_import_requires_login(client):
    response = client.post(reverse("catalog:material_import"))
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_station_import_forbidden_for_non_admin(client):
    client.force_login(UserFactory(account_role="manager"))
    assert client.post(reverse("catalog:station_import")).status_code == 403


def test_material_import_forbidden_for_non_admin(client):
    client.force_login(UserFactory(account_role="manager"))
    assert client.post(reverse("catalog:material_import")).status_code == 403


def test_workshoprole_import_forbidden_for_non_admin(client):
    client.force_login(UserFactory(account_role="operator"))
    response = client.post(reverse("catalog:library_import", args=["workshop-role"]))
    assert response.status_code == 403


def test_station_import_allowed_for_admin_and_populates(client):
    admin = UserFactory(account_role="admin")
    import_library("station-category", admin.workshop)
    import_library("operation-type", admin.workshop)
    client.force_login(admin)

    response = client.post(reverse("catalog:station_import"))

    assert response.status_code == 200
    assert "Import complete" in response.content.decode()
    assert Station.objects.filter(workshop=admin.workshop).exists()


def test_material_import_allowed_for_admin_and_populates(client):
    admin = UserFactory(account_role="admin")
    import_library("material-category", admin.workshop)
    import_library("unit-type", admin.workshop)
    client.force_login(admin)

    response = client.post(reverse("catalog:material_import"))

    assert response.status_code == 200
    assert "Import complete" in response.content.decode()
    assert MaterialVariant.objects.filter(material__workshop=admin.workshop).exists()


# --------------------------------------------------------------------------- #
# Tables — rendering, empty state, workshop scoping
# --------------------------------------------------------------------------- #


def test_workshoprole_table_renders_clearances(client):
    admin = UserFactory(account_role="admin")
    op = OperationTypeFactory(workshop=admin.workshop, name="Cutting")
    WorkshopRoleFactory(workshop=admin.workshop, name="Bench Joiner", default_clearances=[op])
    client.force_login(admin)

    body = client.get(reverse("catalog:library_table", args=["workshop-role"])).content.decode()

    assert "Bench Joiner" in body
    assert "Cutting" in body


def test_station_table_renders_columns(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    category = StationCategoryFactory(workshop=ws, name="Sawing", colour="#123456")
    op = OperationTypeFactory(workshop=ws, name="Ripping")
    station = StationFactory(
        workshop=ws, name="Panel Saw", category=category, supported_operations=[op]
    )
    client.force_login(admin)

    body = client.get(reverse("catalog:station_table")).content.decode()

    assert station.code in body
    assert "Panel Saw" in body
    assert "Sawing" in body
    assert "Ripping" in body


def test_station_table_empty_state(client):
    admin = UserFactory(account_role="admin")
    client.force_login(admin)

    body = client.get(reverse("catalog:station_table")).content.decode()

    assert "No records imported yet" in body
    # Guard against a multi-line {# #} comment leaking as literal text.
    assert "{#" not in body
    assert "{% comment %}" not in body


def test_material_table_grouped_display_and_bare_indicator(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    oak = MaterialFactory(workshop=ws, name="Oak Board")
    MaterialVariantFactory(material=oak, spec_label="25x100")
    MaterialVariantFactory(material=oak, spec_label="25x150")
    MaterialFactory(workshop=ws, name="Wood Filler")  # bare — no variants
    client.force_login(admin)

    body = client.get(reverse("catalog:material_table")).content.decode()

    assert "Oak Board" in body
    assert "25x100" in body
    assert "25x150" in body
    assert "Wood Filler" in body
    assert "No variants" in body


def test_material_table_empty_state(client):
    admin = UserFactory(account_role="admin")
    client.force_login(admin)

    body = client.get(reverse("catalog:material_table")).content.decode()

    assert "No records imported yet" in body


def test_station_table_is_workshop_scoped(client):
    admin = UserFactory(account_role="admin")
    other = WorkshopFactory()
    StationFactory(workshop=other, name="Foreign Saw")
    StationFactory(workshop=admin.workshop, name="Own Saw")
    client.force_login(admin)

    body = client.get(reverse("catalog:station_table")).content.decode()

    assert "Own Saw" in body
    assert "Foreign Saw" not in body


def test_station_table_readable_by_non_admin_member(client):
    manager = UserFactory(account_role="manager")
    StationFactory(workshop=manager.workshop, name="Shared Saw")
    client.force_login(manager)

    response = client.get(reverse("catalog:station_table"))

    assert response.status_code == 200
    assert "Shared Saw" in response.content.decode()


# --------------------------------------------------------------------------- #
# Tables — pagination
# --------------------------------------------------------------------------- #


def test_station_table_paginates_50_per_page(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    category = StationCategoryFactory(workshop=ws)
    for _ in range(55):
        StationFactory(workshop=ws, category=category)
    client.force_login(admin)

    page1 = client.get(reverse("catalog:station_table"))
    assert len(page1.context["page_obj"]) == 50
    assert page1.context["page_obj"].paginator.num_pages == 2

    page2 = client.get(reverse("catalog:station_table"), {"page": 2})
    assert len(page2.context["page_obj"]) == 5


def test_workshoprole_table_paginates_50_per_page(client):
    admin = UserFactory(account_role="admin")
    for _ in range(55):
        WorkshopRoleFactory(workshop=admin.workshop)
    client.force_login(admin)

    page1 = client.get(reverse("catalog:library_table", args=["workshop-role"]))
    assert len(page1.context["page_obj"]) == 50
    assert page1.context["page_obj"].paginator.num_pages == 2


def test_material_table_paginates_by_material_variants_not_split(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    category = MaterialCategoryFactory(workshop=ws)
    unit = UnitTypeFactory(workshop=ws)
    for _ in range(51):
        material = MaterialFactory(workshop=ws, category=category, unit=unit)
        MaterialVariantFactory(material=material)
        MaterialVariantFactory(material=material)
    client.force_login(admin)

    page1 = client.get(reverse("catalog:material_table"))
    assert page1.context["page_obj"].paginator.count == 51
    assert len(page1.context["page_obj"]) == 50  # 50 Materials on page 1
    # Each Material keeps both variants together — variants are never split.
    assert all(len(row["variants"]) == 2 for row in page1.context["rows"])

    page2 = client.get(reverse("catalog:material_table"), {"page": 2})
    assert len(page2.context["page_obj"]) == 1


# --------------------------------------------------------------------------- #
# Tables — search
# --------------------------------------------------------------------------- #


def test_station_table_search_by_name(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    StationFactory(workshop=ws, name="Panel Saw")
    StationFactory(workshop=ws, name="Assembly Bench")
    client.force_login(admin)

    response = client.get(reverse("catalog:station_table"), {"search": "saw"})

    names = [s.name for s in response.context["page_obj"]]
    assert names == ["Panel Saw"]


def test_material_table_search_by_spec_returns_parent_with_all_variants(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    oak = MaterialFactory(workshop=ws, name="Oak Board")
    MaterialVariantFactory(material=oak, spec_label="special-25")
    MaterialVariantFactory(material=oak, spec_label="plain-30")
    MaterialFactory(workshop=ws, name="Pine Board")
    client.force_login(admin)

    response = client.get(reverse("catalog:material_table"), {"search": "special"})

    rows = response.context["rows"]
    assert len(rows) == 1
    assert rows[0]["material"].name == "Oak Board"
    # A spec_label match returns the whole parent — both variants, not only the match.
    assert len(rows[0]["variants"]) == 2


# --------------------------------------------------------------------------- #
# Tables — sort
# --------------------------------------------------------------------------- #


def test_station_table_sort_by_name_desc(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    category = StationCategoryFactory(workshop=ws)
    for name in ("Alpha", "Bravo", "Charlie"):
        StationFactory(workshop=ws, name=name, category=category)
    client.force_login(admin)

    response = client.get(reverse("catalog:station_table"), {"sort": "name", "dir": "desc"})

    names = [s.name for s in response.context["page_obj"]]
    assert names == ["Charlie", "Bravo", "Alpha"]


def test_station_table_rejects_unknown_sort_field(client):
    admin = UserFactory(account_role="admin")
    StationFactory(workshop=admin.workshop)
    client.force_login(admin)

    response = client.get(reverse("catalog:station_table"), {"sort": "workshop_id; drop"})

    assert response.status_code == 200
    assert response.context["sort"] == "code"


def test_material_table_sort_by_name(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    for name in ("Beech", "Ash", "Cedar"):
        MaterialFactory(workshop=ws, name=name)
    client.force_login(admin)

    asc = client.get(reverse("catalog:material_table"), {"dir": "asc"})
    assert [r["material"].name for r in asc.context["rows"]] == ["Ash", "Beech", "Cedar"]

    desc = client.get(reverse("catalog:material_table"), {"dir": "desc"})
    assert [r["material"].name for r in desc.context["rows"]] == ["Cedar", "Beech", "Ash"]


# --------------------------------------------------------------------------- #
# Tables — filters
# --------------------------------------------------------------------------- #


def test_station_table_category_and_status_filters_combine(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    sawing = StationCategoryFactory(workshop=ws, name="Sawing")
    sanding = StationCategoryFactory(workshop=ws, name="Sanding")
    StationFactory(workshop=ws, name="Active Saw", category=sawing, status="active")
    StationFactory(workshop=ws, name="Offline Saw", category=sawing, status="offline")
    StationFactory(workshop=ws, name="Active Sander", category=sanding, status="active")
    client.force_login(admin)

    response = client.get(
        reverse("catalog:station_table"), {"category": sawing.pk, "status": "active"}
    )

    names = [s.name for s in response.context["page_obj"]]
    assert names == ["Active Saw"]


def test_material_low_stock_filter_uses_current_stock_not_available(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    low = MaterialFactory(workshop=ws, name="Low Board")
    MaterialVariantFactory(
        material=low, current_stock=Decimal("5"), min_threshold=Decimal("10"), reserved=Decimal("0")
    )
    reserved_heavy = MaterialFactory(workshop=ws, name="Reserved Board")
    # available (20 - 15 = 5) is below threshold, but current_stock (20) is not —
    # so this must NOT count as low: reserved/available play no part.
    variant = MaterialVariantFactory(
        material=reserved_heavy,
        current_stock=Decimal("20"),
        min_threshold=Decimal("10"),
        reserved=Decimal("15"),
    )
    client.force_login(admin)

    response = client.get(reverse("catalog:material_table"), {"low_stock": "1"})

    names = [row["material"].name for row in response.context["rows"]]
    assert names == ["Low Board"]
    assert variant.stock_status == "clear"  # confirms the reserved-heavy variant is not low


def test_material_low_stock_filter_narrows_variants_and_hides_clear_materials(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    mixed = MaterialFactory(workshop=ws, name="Mixed Board")
    MaterialVariantFactory(
        material=mixed, spec_label="low", current_stock=Decimal("1"), min_threshold=Decimal("5")
    )
    MaterialVariantFactory(
        material=mixed, spec_label="clear", current_stock=Decimal("50"), min_threshold=Decimal("5")
    )
    all_clear = MaterialFactory(workshop=ws, name="Healthy Board")
    MaterialVariantFactory(
        material=all_clear, current_stock=Decimal("50"), min_threshold=Decimal("5")
    )
    client.force_login(admin)

    response = client.get(reverse("catalog:material_table"), {"low_stock": "1"})

    rows = response.context["rows"]
    assert [row["material"].name for row in rows] == ["Mixed Board"]  # healthy hidden entirely
    # Only the low variant of the mixed Material is shown under the filter.
    assert [v.spec_label for v in rows[0]["variants"]] == ["low"]


def test_material_category_and_unit_filters_combine(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    hardwood = MaterialCategoryFactory(workshop=ws, name="Hardwood")
    softwood = MaterialCategoryFactory(workshop=ws, name="Softwood")
    length = UnitTypeFactory(workshop=ws, name="Length", abbreviation="len")
    sheet = UnitTypeFactory(workshop=ws, name="Sheet", abbreviation="sht")
    MaterialFactory(workshop=ws, name="Oak", category=hardwood, unit=length)
    MaterialFactory(workshop=ws, name="Oak Ply", category=hardwood, unit=sheet)
    MaterialFactory(workshop=ws, name="Pine", category=softwood, unit=length)
    client.force_login(admin)

    response = client.get(
        reverse("catalog:material_table"), {"category": hardwood.pk, "unit": length.pk}
    )

    names = [row["material"].name for row in response.context["rows"]]
    assert names == ["Oak"]


# --------------------------------------------------------------------------- #
# Tables — search + sort + filter + pagination compose
# --------------------------------------------------------------------------- #


def test_station_table_search_sort_filter_compose(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    sawing = StationCategoryFactory(workshop=ws, name="Sawing")
    sanding = StationCategoryFactory(workshop=ws, name="Sanding")
    StationFactory(workshop=ws, name="Panel Saw", category=sawing, status="active")
    StationFactory(workshop=ws, name="Bench Saw", category=sawing, status="active")
    StationFactory(workshop=ws, name="Table Saw", category=sawing, status="offline")
    StationFactory(workshop=ws, name="Belt Sander", category=sanding, status="active")
    client.force_login(admin)

    response = client.get(
        reverse("catalog:station_table"),
        {"search": "saw", "category": sawing.pk, "status": "active", "sort": "name", "dir": "desc"},
    )

    names = [s.name for s in response.context["page_obj"]]
    # "saw" matches the three Saws; Sawing + active drops "Table Saw"; desc sort orders the rest.
    assert names == ["Panel Saw", "Bench Saw"]
