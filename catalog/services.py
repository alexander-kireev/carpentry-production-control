"""Library import engine (C1, extended for the name-resolving types in C2).

Reads a bundled CSV, then validates and inserts each row **independently** — a
bad, unresolvable, or duplicate row is skipped and reported, never aborting the
rest — and returns an ``ImportSummary``.

C1 covers the five dependency-free types. C2 adds:

- **Name resolution** — FK columns (e.g. Station ``category``) and M2M columns
  (e.g. WorkshopRole ``default_clearances``, Station ``supported_operations``)
  hold *names* that resolve to related rows **within the same workshop** (D-126;
  names collide across workshops). An unresolvable reference skips the whole row.
- **Reserved-seed guard** — a type may reserve system names (WorkshopRole's
  "Admin"/"undefined") that can never be imported into a workshop.
- **Material + MaterialVariant** — one combined file/action (``import_materials``)
  grouping rows by Material name; a row with no variant columns is a bare Material
  (0 variants is valid — D-121).

Idempotent: a row already present (by the type's uniqueness keys, scoped to the
workshop) is skipped as a duplicate; nothing is overwritten. Re-running an import
therefore only fills gaps, so an admin can restore the defaults after deleting
rows without creating duplicates.

Request-agnostic in the A1/A2 service tradition: it takes the domain ``Workshop``
and returns a domain result, with no ``request``/session/HTTP coupling, so it
stays callable from a management command or a test. Every insert, duplicate
check, and name resolution is scoped to the given workshop (D-126); another
workshop's rows — and the NULL-workshop system sentinels — are never read or
touched.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction

from catalog.library_config import (
    RowError,
    get_library_type,
    parse_decimal,
    parse_lot_sizes,
)
from catalog.models import Material, MaterialCategory, MaterialVariant, UnitType


@dataclass
class SkippedRow:
    row: int
    name: str
    reason: str


@dataclass
class ImportSummary:
    imported: int = 0
    skipped: list[SkippedRow] = field(default_factory=list)
    # A file-level problem (missing file, or columns that don't match). When set,
    # no rows are imported — it is not a per-row skip.
    error: str | None = None

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


def import_library(slug: str, workshop, *, base_dir: Path | None = None) -> ImportSummary:
    """Import ``slug``'s bundled default library into ``workshop``.

    ``base_dir`` overrides the bundled-file directory (tests point it at a temp
    directory of crafted CSVs). A malformed or missing file is reported via
    ``ImportSummary.error`` — it never raises, so the caller cannot 500.
    """
    lib = get_library_type(slug)
    base = Path(base_dir) if base_dir is not None else Path(settings.LIBRARY_IMPORT_DIR)
    path = base / f"{slug}.csv"
    summary = ImportSummary()

    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = {name.strip() for name in (reader.fieldnames or [])}
            if header != lib.csv_columns:
                summary.error = "File columns don't match the expected format for this library."
                return summary
            for index, raw in enumerate(reader, start=1):
                _process_row(lib, workshop, raw, index, summary)
    except FileNotFoundError:
        summary.error = "The default library file is missing."
    except (OSError, csv.Error, UnicodeDecodeError):
        summary.error = "The default library file could not be read."

    return summary


def _process_row(lib, workshop, raw, row_no, summary):
    name_seen = (raw.get("name") or "").strip()

    # Reserved-seed guard (WorkshopRole): the global system sentinels can never be
    # imported into a workshop. The (workshop, name) constraint wouldn't catch a
    # workshop-scoped duplicate of a NULL-workshop sentinel, so guard by name here.
    if lib.reserved_names and name_seen.lower() in lib.reserved_names:
        summary.skipped.append(SkippedRow(row_no, name_seen, lib.reserved_reason))
        return

    cleaned = {}
    for field_cfg in lib.fields:
        value = (raw.get(field_cfg.name) or "").strip()
        if not value:
            if field_cfg.required:
                summary.skipped.append(
                    SkippedRow(row_no, name_seen, f'missing required field "{field_cfg.name}"')
                )
                return
            cleaned[field_cfg.name] = field_cfg.empty
            continue
        try:
            cleaned[field_cfg.name] = field_cfg.parse(value)
        except RowError as exc:
            summary.skipped.append(SkippedRow(row_no, name_seen, str(exc)))
            return

    # Skip a duplicate before spending resolution work (idempotent re-run).
    reason = _duplicate_reason(lib, workshop, cleaned)
    if reason:
        summary.skipped.append(SkippedRow(row_no, name_seen, reason))
        return

    # Resolve single-row references (FK) within the workshop. Unresolvable → skip.
    for ref in lib.ref_fields:
        value = (raw.get(ref.name) or "").strip()
        if not value:
            if ref.required:
                summary.skipped.append(
                    SkippedRow(row_no, name_seen, f'missing required field "{ref.name}"')
                )
                return
            cleaned[ref.name] = None
            continue
        target = _resolve_name(ref.target, workshop, value)
        if target is None:
            summary.skipped.append(
                SkippedRow(
                    row_no, name_seen, f'{ref.name} "{value}" not found in {ref.target_label}'
                )
            )
            return
        cleaned[ref.name] = target

    # Resolve list references (M2M) within the workshop. Zero names is allowed; any
    # unresolvable name skips the whole row. Collected now, set after create.
    m2m_values = {}
    for m2m in lib.m2m_fields:
        names = [
            part.strip()
            for part in (raw.get(m2m.name) or "").split(m2m.delimiter)
            if part.strip()
        ]
        resolved = []
        for element in names:
            target = _resolve_name(m2m.target, workshop, element)
            if target is None:
                summary.skipped.append(
                    SkippedRow(
                        row_no,
                        name_seen,
                        f'{m2m.singular} "{element}" not found in {m2m.target_label}',
                    )
                )
                return
            resolved.append(target)
        m2m_values[m2m.name] = resolved

    try:
        with transaction.atomic():
            obj = lib.model.objects.create(workshop=workshop, **cleaned)
            for relation, targets in m2m_values.items():
                if targets:
                    getattr(obj, relation).set(targets)
    except IntegrityError:
        # DB-level backstop for anything the pre-checks miss (e.g. a concurrent
        # insert). The unique constraints are the real guarantee; this only turns
        # a would-be 500 into a clean skip.
        summary.skipped.append(
            SkippedRow(row_no, name_seen, "duplicate, already exists in this library")
        )
        return

    summary.imported += 1


def _duplicate_reason(lib, workshop, cleaned):
    """First uniqueness key (scoped to ``workshop``) that ``cleaned`` collides on."""
    for fields_tuple, message in lib.unique_keys:
        lookup = {name: cleaned[name] for name in fields_tuple}
        if lib.model.objects.filter(workshop=workshop, **lookup).exists():
            return message
    return None


def _resolve_name(target, workshop, value):
    """The workshop's ``target`` row named ``value``, or ``None``.

    Scoped to ``workshop`` (D-126): names legitimately collide across workshops,
    and the NULL-workshop system sentinels are deliberately excluded — an import
    reference resolves only against the requesting workshop's own rows.
    """
    return target.objects.filter(workshop=workshop, name=value).first()


# --------------------------------------------------------------------------- #
# Material + MaterialVariant — one combined file, grouped by Material name
# --------------------------------------------------------------------------- #

# Both levels share one file (workshop/behaviour.md): Material columns (name,
# category, unit) and MaterialVariant columns (spec_label, current_stock,
# min_threshold, lot_sizes).
MATERIAL_COLUMNS = frozenset(
    {"name", "category", "unit", "spec_label", "current_stock", "min_threshold", "lot_sizes"}
)
_VARIANT_COLUMNS = ("spec_label", "current_stock", "min_threshold", "lot_sizes")


def import_materials(workshop, *, base_dir: Path | None = None) -> ImportSummary:
    """Import the combined Material + MaterialVariant default library into ``workshop``.

    Rows are grouped by Material name. The first row for a name establishes the
    Material (its ``category``/``unit`` resolve against the workshop's own
    MaterialCategory/UnitType); a row with no variant columns creates a **bare
    Material** (0 variants is valid — D-121); further rows attach variants.
    ``lot_sizes`` is stored as a JSON list of numbers (storage only — purchase-order
    lot validation is Slice E). Idempotent: an existing Material (by name) or
    variant (by spec_label within the Material) is skipped, never overwritten.
    Never raises — a file-level problem is reported via ``ImportSummary.error``.

    ``base_dir`` overrides the bundled-file directory (tests point it at crafted CSVs).
    """
    base = Path(base_dir) if base_dir is not None else Path(settings.LIBRARY_IMPORT_DIR)
    path = base / "material.csv"
    summary = ImportSummary()

    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = {name.strip() for name in (reader.fieldnames or [])}
            if header != MATERIAL_COLUMNS:
                summary.error = "File columns don't match the expected format for this library."
                return summary
            for index, raw in enumerate(reader, start=1):
                _process_material_row(workshop, raw, index, summary)
    except FileNotFoundError:
        summary.error = "The default library file is missing."
    except (OSError, csv.Error, UnicodeDecodeError):
        summary.error = "The default library file could not be read."

    return summary


def _process_material_row(workshop, raw, row_no, summary):
    name = (raw.get("name") or "").strip()
    if not name:
        summary.skipped.append(SkippedRow(row_no, "", 'missing required field "name"'))
        return

    # Group by name: an existing Material (created by an earlier row this run, or a
    # prior import) is reused; its category/unit are not re-derived from this row.
    material = Material.objects.filter(workshop=workshop, name=name).first()

    new_material_fields = None
    if material is None:
        new_material_fields = _resolve_material_level(workshop, raw, name, row_no, summary)
        if new_material_fields is None:
            return

    variant_cells = {col: (raw.get(col) or "").strip() for col in _VARIANT_COLUMNS}
    if not any(variant_cells.values()):
        # Bare-Material row (D-121): no variant columns filled.
        if material is not None:
            summary.skipped.append(
                SkippedRow(row_no, name, "duplicate, already exists in this library")
            )
            return
        try:
            with transaction.atomic():
                Material.objects.create(workshop=workshop, **new_material_fields)
        except IntegrityError:
            summary.skipped.append(
                SkippedRow(row_no, name, "duplicate, already exists in this library")
            )
            return
        summary.imported += 1
        return

    variant_values = _clean_variant(variant_cells, name, row_no, summary)
    if variant_values is None:
        return

    if material is not None and material.variants.filter(
        spec_label=variant_values["spec_label"]
    ).exists():
        summary.skipped.append(
            SkippedRow(
                row_no,
                name,
                f'duplicate variant "{variant_values["spec_label"]}", '
                "already exists for this material",
            )
        )
        return

    try:
        with transaction.atomic():
            if material is None:
                material = Material.objects.create(workshop=workshop, **new_material_fields)
            MaterialVariant.objects.create(material=material, **variant_values)
    except IntegrityError:
        summary.skipped.append(
            SkippedRow(row_no, name, "duplicate variant, already exists for this material")
        )
        return

    summary.imported += 1


def _resolve_material_level(workshop, raw, name, row_no, summary):
    """Resolve a new Material's category + unit, or record a skip and return None."""
    category_name = (raw.get("category") or "").strip()
    if not category_name:
        summary.skipped.append(SkippedRow(row_no, name, 'missing required field "category"'))
        return None
    category = _resolve_name(MaterialCategory, workshop, category_name)
    if category is None:
        summary.skipped.append(
            SkippedRow(row_no, name, f'category "{category_name}" not found in Material Categories')
        )
        return None

    unit_name = (raw.get("unit") or "").strip()
    if not unit_name:
        summary.skipped.append(SkippedRow(row_no, name, 'missing required field "unit"'))
        return None
    unit = _resolve_name(UnitType, workshop, unit_name)
    if unit is None:
        summary.skipped.append(
            SkippedRow(row_no, name, f'unit "{unit_name}" not found in Unit Types')
        )
        return None

    return {"name": name, "category": category, "unit": unit}


def _clean_variant(variant_cells, name, row_no, summary):
    """Validate + parse the four variant columns (all required), or None on skip."""
    values = {}
    for col, parser in (
        ("spec_label", None),
        ("current_stock", parse_decimal),
        ("min_threshold", parse_decimal),
        ("lot_sizes", parse_lot_sizes),
    ):
        cell = variant_cells[col]
        if not cell:
            summary.skipped.append(SkippedRow(row_no, name, f'missing required field "{col}"'))
            return None
        if parser is None:
            values[col] = cell
            continue
        try:
            values[col] = parser(cell)
        except RowError as exc:
            summary.skipped.append(SkippedRow(row_no, name, str(exc)))
            return None
    return values
