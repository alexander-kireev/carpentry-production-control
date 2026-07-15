"""Library import engine (C1).

Reads the bundled CSV for one of the five dependency-free library types, then
validates and inserts each row **independently** — a bad or duplicate row is
skipped and reported, never aborting the rest — and returns an ``ImportSummary``.

Idempotent: a row already present (by the type's uniqueness keys, scoped to the
workshop) is skipped as a duplicate; nothing is overwritten. Re-running an import
therefore only fills gaps, so an admin can restore the defaults after deleting
rows without creating duplicates.

Request-agnostic in the A1/A2 service tradition: it takes the domain ``Workshop``
and returns a domain result, with no ``request``/session/HTTP coupling, so it
stays callable from a management command or a test. Every insert and duplicate
check is scoped to the given workshop (D-126); another workshop's rows — and the
NULL-workshop system sentinels — are never read or touched.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction

from catalog.library_config import RowError, get_library_type


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

    reason = _duplicate_reason(lib, workshop, cleaned)
    if reason:
        summary.skipped.append(SkippedRow(row_no, name_seen, reason))
        return

    try:
        with transaction.atomic():
            lib.model.objects.create(workshop=workshop, **cleaned)
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
