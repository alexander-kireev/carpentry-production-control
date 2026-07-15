"""Library import engine + read-only table views (Slice C1).

Covers the Full-protocol contract for bulk import: valid import, per-row
skip-and-report (missing field, bad value, duplicate on any uniqueness key),
idempotent re-run, per-workshop isolation (D-126), malformed/missing-file
handling (no 500), and the admin-only permission boundary; plus the read-only
table's rendering, empty state, pagination, search, sort, OperationType filter,
and their composition. Runs against PostgreSQL (config.settings.test).
"""

import csv
from datetime import time

import pytest
from django.conf import settings
from django.urls import reverse

from catalog.library_config import DISPLAY_LIBRARY_TYPES
from catalog.models import MaterialCategory, OperationType, ShiftDefinition
from catalog.services import import_library
from tests.factories import (
    OperationTypeFactory,
    ShiftDefinitionFactory,
    StationCategoryFactory,
    UserFactory,
    WorkshopFactory,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _write_csv(directory, slug, header, rows):
    path = directory / f"{slug}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _bundled_row_count(slug):
    path = settings.LIBRARY_IMPORT_DIR / f"{slug}.csv"
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def table_url(slug):
    return reverse("catalog:library_table", args=[slug])


def import_url(slug):
    return reverse("catalog:library_import", args=[slug])


# --------------------------------------------------------------------------- #
# Engine — valid import (scoped to the workshop)
# --------------------------------------------------------------------------- #


def test_valid_import_creates_rows_scoped_to_workshop(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "operation-type",
        ["name", "description", "is_production"],
        [["Cutting", "Cut to size", "true"], ["Build Planning", "Plan a build", "false"]],
    )
    summary = import_library("operation-type", ws, base_dir=tmp_path)

    assert summary.error is None
    assert summary.imported == 2
    assert summary.skipped == []
    assert OperationType.objects.filter(workshop=ws).count() == 2
    assert OperationType.objects.get(workshop=ws, name="Build Planning").is_production is False


def test_optional_field_may_be_blank(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "operation-type",
        ["name", "description", "is_production"],
        [["Sanding", "", "true"]],
    )
    summary = import_library("operation-type", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert OperationType.objects.get(workshop=ws, name="Sanding").description == ""


def test_shift_days_canonicalised_on_import(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "shift-definition",
        ["name", "start_time", "end_time", "days"],
        [["Day", "08:00", "16:00", "fri;mon;wed"]],
    )
    summary = import_library("shift-definition", ws, base_dir=tmp_path)

    assert summary.imported == 1
    shift = ShiftDefinition.objects.get(workshop=ws, name="Day")
    assert shift.days == ["mon", "wed", "fri"]
    assert shift.start_time == time(8, 0)


# --------------------------------------------------------------------------- #
# Engine — per-row skip and report
# --------------------------------------------------------------------------- #


def test_missing_required_field_skipped_and_reported(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "operation-type",
        ["name", "description", "is_production"],
        [["Cutting", "ok", "true"], ["", "no name", "true"]],
    )
    summary = import_library("operation-type", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert summary.skipped_count == 1
    skipped = summary.skipped[0]
    assert skipped.row == 2
    assert "missing required field" in skipped.reason
    assert "name" in skipped.reason


def test_unparseable_value_skipped_and_reported(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "operation-type",
        ["name", "description", "is_production"],
        [["Cutting", "ok", "maybe"]],
    )
    summary = import_library("operation-type", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert summary.skipped_count == 1
    assert "invalid value" in summary.skipped[0].reason
    assert OperationType.objects.filter(workshop=ws).count() == 0


def test_bad_time_and_bad_day_skipped(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "shift-definition",
        ["name", "start_time", "end_time", "days"],
        [
            ["Bad time", "8am", "16:00", "mon"],
            ["Bad day", "08:00", "16:00", "mon;funday"],
        ],
    )
    summary = import_library("shift-definition", ws, base_dir=tmp_path)

    assert summary.imported == 0
    reasons = {row.name: row.reason for row in summary.skipped}
    assert "invalid time" in reasons["Bad time"]
    assert "invalid day" in reasons["Bad day"]


def test_duplicate_name_skipped_and_reported(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "material-category",
        ["name"],
        [["Hardwood"], ["Hardwood"]],
    )
    summary = import_library("material-category", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert summary.skipped_count == 1
    assert "duplicate name" in summary.skipped[0].reason
    assert MaterialCategory.objects.filter(workshop=ws).count() == 1


def test_duplicate_abbreviation_skipped(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "unit-type",
        ["name", "abbreviation"],
        [["Metre", "m"], ["Milli", "m"]],
    )
    summary = import_library("unit-type", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert "duplicate abbreviation" in summary.skipped[0].reason


def test_duplicate_colour_skipped(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "station-category",
        ["name", "colour"],
        [["Sawing", "#2980b9"], ["Machining", "#2980b9"]],
    )
    summary = import_library("station-category", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert "duplicate colour" in summary.skipped[0].reason


def test_duplicate_shift_window_skipped_regardless_of_name(tmp_path):
    ws = WorkshopFactory()
    _write_csv(
        tmp_path,
        "shift-definition",
        ["name", "start_time", "end_time", "days"],
        [
            ["Day A", "08:00", "16:00", "mon;tue"],
            ["Day B", "08:00", "16:00", "tue;mon"],  # same window, reordered days
        ],
    )
    summary = import_library("shift-definition", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert "duplicate shift window" in summary.skipped[0].reason


# --------------------------------------------------------------------------- #
# Engine — idempotency, restore, isolation
# --------------------------------------------------------------------------- #


def test_reimport_is_idempotent(tmp_path):
    ws = WorkshopFactory()
    _write_csv(tmp_path, "material-category", ["name"], [["Hardwood"], ["Softwood"]])
    import_library("material-category", ws, base_dir=tmp_path)
    summary = import_library("material-category", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert summary.skipped_count == 2
    assert MaterialCategory.objects.filter(workshop=ws).count() == 2


def test_manual_addition_survives_reimport(tmp_path):
    ws = WorkshopFactory()
    _write_csv(tmp_path, "material-category", ["name"], [["Hardwood"], ["Softwood"]])
    import_library("material-category", ws, base_dir=tmp_path)
    MaterialCategory.objects.create(workshop=ws, name="Reclaimed Oak")

    summary = import_library("material-category", ws, base_dir=tmp_path)

    assert summary.imported == 0
    assert MaterialCategory.objects.filter(workshop=ws, name="Reclaimed Oak").exists()
    assert MaterialCategory.objects.filter(workshop=ws).count() == 3


def test_deleted_seed_row_restored_on_reimport(tmp_path):
    ws = WorkshopFactory()
    _write_csv(tmp_path, "material-category", ["name"], [["Hardwood"], ["Softwood"]])
    import_library("material-category", ws, base_dir=tmp_path)
    MaterialCategory.objects.filter(workshop=ws, name="Hardwood").delete()

    summary = import_library("material-category", ws, base_dir=tmp_path)

    assert summary.imported == 1
    assert MaterialCategory.objects.filter(workshop=ws, name="Hardwood").exists()


def test_import_is_workshop_isolated(tmp_path):
    ws1, ws2 = WorkshopFactory(), WorkshopFactory()
    _write_csv(tmp_path, "material-category", ["name"], [["Hardwood"], ["Softwood"]])

    import_library("material-category", ws1, base_dir=tmp_path)
    assert MaterialCategory.objects.filter(workshop=ws1).count() == 2
    assert MaterialCategory.objects.filter(workshop=ws2).count() == 0

    # The same names import independently into ws2 — no cross-workshop collision.
    summary = import_library("material-category", ws2, base_dir=tmp_path)
    assert summary.imported == 2
    assert MaterialCategory.objects.filter(workshop=ws2).count() == 2


# --------------------------------------------------------------------------- #
# Engine — malformed / missing file (no 500)
# --------------------------------------------------------------------------- #


def test_header_mismatch_reported_no_insert(tmp_path):
    ws = WorkshopFactory()
    _write_csv(tmp_path, "operation-type", ["name", "description"], [["Cutting", "ok"]])
    summary = import_library("operation-type", ws, base_dir=tmp_path)

    assert summary.error is not None
    assert summary.imported == 0
    assert OperationType.objects.filter(workshop=ws).count() == 0


def test_missing_file_reported(tmp_path):
    ws = WorkshopFactory()
    summary = import_library("operation-type", ws, base_dir=tmp_path)

    assert summary.error is not None
    assert summary.imported == 0


# --------------------------------------------------------------------------- #
# Bundled default libraries — the shipped fixtures import cleanly
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lib", DISPLAY_LIBRARY_TYPES, ids=lambda lt: lt.slug)
def test_bundled_default_library_imports_cleanly(lib):
    ws = WorkshopFactory()
    summary = import_library(lib.slug, ws)

    assert summary.error is None
    assert summary.skipped == []
    expected = _bundled_row_count(lib.slug)
    assert expected > 0
    assert summary.imported == expected
    assert lib.model.objects.filter(workshop=ws).count() == expected


# --------------------------------------------------------------------------- #
# Views — permission boundary
# --------------------------------------------------------------------------- #


def test_import_requires_login(client):
    response = client.post(import_url("operation-type"))
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_import_forbidden_for_non_admin(client):
    client.force_login(UserFactory(account_role="manager"))
    response = client.post(import_url("operation-type"))
    assert response.status_code == 403


def test_import_allowed_for_admin_and_populates(client):
    admin = UserFactory(account_role="admin")
    client.force_login(admin)
    response = client.post(import_url("operation-type"))

    assert response.status_code == 200
    assert "Import complete" in response.content.decode()
    assert OperationType.objects.filter(workshop=admin.workshop).count() > 0


def test_import_view_is_idempotent(client):
    admin = UserFactory(account_role="admin")
    client.force_login(admin)
    client.post(import_url("operation-type"))
    count = OperationType.objects.filter(workshop=admin.workshop).count()
    response = client.post(import_url("operation-type"))

    assert "0 imported" in response.content.decode()
    assert OperationType.objects.filter(workshop=admin.workshop).count() == count


def test_unknown_type_is_404(client):
    client.force_login(UserFactory(account_role="admin"))
    assert client.get("/library/not-a-type").status_code == 404


# --------------------------------------------------------------------------- #
# Views — read-only table
# --------------------------------------------------------------------------- #


def test_table_requires_login(client):
    response = client.get(table_url("operation-type"))
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_table_readable_by_non_admin_member(client):
    manager = UserFactory(account_role="manager")
    OperationTypeFactory(workshop=manager.workshop, name="Cutting")
    client.force_login(manager)
    response = client.get(table_url("operation-type"))

    assert response.status_code == 200
    assert "Cutting" in response.content.decode()


def test_table_empty_state(client):
    admin = UserFactory(account_role="admin")
    client.force_login(admin)
    response = client.get(table_url("operation-type"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "No records imported yet" in body
    # Guard against multi-line {# #} leaking as literal text (use {% comment %}).
    assert "{#" not in body
    assert "{% comment %}" not in body


def test_station_category_table_renders_colour_swatch(client):
    admin = UserFactory(account_role="admin")
    StationCategoryFactory(workshop=admin.workshop, name="Sawing", colour="#2980b9")
    client.force_login(admin)
    body = client.get(table_url("station-category")).content.decode()

    assert "swatch" in body
    assert "#2980b9" in body


def test_shift_definition_table_renders_time_and_days(client):
    admin = UserFactory(account_role="admin")
    ShiftDefinitionFactory(
        workshop=admin.workshop,
        name="Day",
        start_time=time(8, 0),
        end_time=time(16, 30),
        days=["mon", "tue"],
    )
    client.force_login(admin)
    body = client.get(table_url("shift-definition")).content.decode()

    assert "08:00" in body
    assert "16:30" in body
    assert "Mon" in body


def test_table_is_workshop_scoped(client):
    admin = UserFactory(account_role="admin")
    other = WorkshopFactory()
    OperationTypeFactory(workshop=other, name="Foreign Op")
    OperationTypeFactory(workshop=admin.workshop, name="Own Op")
    client.force_login(admin)
    body = client.get(table_url("operation-type")).content.decode()

    assert "Own Op" in body
    assert "Foreign Op" not in body


def test_table_pagination_20_per_page(client):
    admin = UserFactory(account_role="admin")
    for _ in range(25):
        OperationTypeFactory(workshop=admin.workshop)
    client.force_login(admin)

    page1 = client.get(table_url("operation-type"))
    assert len(page1.context["page_obj"]) == 20
    assert page1.context["page_obj"].paginator.num_pages == 2

    page2 = client.get(table_url("operation-type"), {"page": 2})
    assert len(page2.context["page_obj"]) == 5


def test_table_search_is_case_insensitive_and_resets_page(client):
    admin = UserFactory(account_role="admin")
    OperationTypeFactory(workshop=admin.workshop, name="Cutting")
    OperationTypeFactory(workshop=admin.workshop, name="Sanding")
    client.force_login(admin)

    response = client.get(table_url("operation-type"), {"search": "CUT", "page": 5})
    names = [obj.name for obj in response.context["page_obj"]]
    assert names == ["Cutting"]
    assert response.context["page_obj"].number == 1


def test_table_sort_desc(client):
    admin = UserFactory(account_role="admin")
    for name in ("Alpha", "Bravo", "Charlie"):
        OperationTypeFactory(workshop=admin.workshop, name=name)
    client.force_login(admin)

    response = client.get(table_url("operation-type"), {"sort": "name", "dir": "desc"})
    names = [obj.name for obj in response.context["page_obj"]]
    assert names == ["Charlie", "Bravo", "Alpha"]


def test_table_rejects_unknown_sort_field(client):
    admin = UserFactory(account_role="admin")
    OperationTypeFactory(workshop=admin.workshop, name="Cutting")
    client.force_login(admin)

    response = client.get(table_url("operation-type"), {"sort": "workshop_id; drop"})
    assert response.status_code == 200
    assert response.context["sort"] == "name"


def test_operation_type_is_production_filter(client):
    admin = UserFactory(account_role="admin")
    OperationTypeFactory(workshop=admin.workshop, name="Cutting", is_production=True)
    OperationTypeFactory(workshop=admin.workshop, name="Assembly", is_production=True)
    OperationTypeFactory(workshop=admin.workshop, name="Build Planning", is_production=False)
    client.force_login(admin)

    all_rows = client.get(table_url("operation-type"))
    assert all_rows.context["page_obj"].paginator.count == 3

    production = client.get(table_url("operation-type"), {"is_production": "true"})
    assert production.context["page_obj"].paginator.count == 2

    non_production = client.get(table_url("operation-type"), {"is_production": "false"})
    names = [obj.name for obj in non_production.context["page_obj"]]
    assert names == ["Build Planning"]


def test_table_search_sort_filter_compose(client):
    admin = UserFactory(account_role="admin")
    ws = admin.workshop
    OperationTypeFactory(workshop=ws, name="Cutting", is_production=True)
    OperationTypeFactory(workshop=ws, name="Cross Cutting", is_production=True)
    OperationTypeFactory(workshop=ws, name="Cut Planning", is_production=False)
    OperationTypeFactory(workshop=ws, name="Assembly", is_production=True)
    client.force_login(admin)

    response = client.get(
        table_url("operation-type"),
        {"search": "cut", "is_production": "true", "sort": "name", "dir": "desc", "page": 1},
    )
    names = [obj.name for obj in response.context["page_obj"]]
    # "cut" matches all three Cut* names; production filter drops "Cut Planning";
    # desc sort orders the remaining two.
    assert names == ["Cutting", "Cross Cutting"]
