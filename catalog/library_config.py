"""Per-type configuration for the library import engine and read-only tables (C1).

The five dependency-free library types are described here as data: their exact
CSV columns, the per-field parsing/validation, the uniqueness keys that drive
duplicate-skip, and the columns (and single optional filter) their read-only
table renders. The engine (``catalog.services.import_library``) and the table
view (``catalog.views``) are generic over this registry, so wiring a new type is
a config entry rather than new code — C2 extends it for the dependency-resolving
types.

A row's file columns must match a type's fields exactly (no column-mapping — MVP,
per ``workshop/behaviour.md`` Library import).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.db import models
from django.db.models import Q

from catalog.models import (
    MaterialCategory,
    OperationType,
    ShiftDefinition,
    StationCategory,
    UnitType,
)


class RowError(ValueError):
    """A per-field parse/validation failure carrying a human-readable reason."""


# --------------------------------------------------------------------------- #
# Field parsers — each takes a stripped, non-empty string and returns the value
# to store, or raises RowError(reason) which the engine reports as a skip.
# --------------------------------------------------------------------------- #

_TRUE = {"true", "yes", "1", "y", "t"}
_FALSE = {"false", "no", "0", "n", "f"}


def parse_text(raw: str) -> str:
    return raw.strip()


def parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise RowError(f'invalid value "{raw}" (expected true or false)')


def parse_time(raw: str):
    value = raw.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise RowError(f'invalid time "{raw}" (expected HH:MM)')


def parse_days(raw: str) -> list[str]:
    order = list(ShiftDefinition.Day.values)
    valid = set(order)
    codes = [code.strip().lower() for code in raw.split(";") if code.strip()]
    if not codes:
        raise RowError("no days given")
    for code in codes:
        if code not in valid:
            raise RowError(f'invalid day "{code}"')
    # Canonicalise (dedupe + weekday order) so it matches what ShiftDefinition.save
    # stores and so the (start, end, days) duplicate check compares like-for-like.
    return sorted(dict.fromkeys(codes), key=order.index)


# --------------------------------------------------------------------------- #
# Config dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Field:
    """One CSV column, which maps 1:1 to a model field of the same name."""

    name: str
    required: bool = True
    parse: Callable[[str], object] = parse_text
    empty: object = ""  # value stored when the field is optional and left blank


@dataclass(frozen=True)
class Column:
    """One column of the read-only table. ``kind`` drives cell rendering."""

    key: str
    label: str
    kind: str = "text"  # text | bool | colour | time | days
    sortable: bool = True


@dataclass(frozen=True)
class FilterOption:
    value: str
    label: str
    q: Q | None = None


@dataclass(frozen=True)
class Filter:
    """A single categorical filter (OperationType.is_production is the only one)."""

    param: str
    label: str
    options: tuple[FilterOption, ...]

    def resolve(self, value: str) -> Q | None:
        for option in self.options:
            if option.value == value:
                return option.q
        return None


@dataclass(frozen=True)
class LibraryType:
    slug: str
    label: str
    model: type[models.Model]
    fields: tuple[Field, ...]
    # Each entry: the field-set that must be jointly unique, and the reason to
    # report when a row collides on it. Mirrors the model's DB constraints.
    unique_keys: tuple[tuple[tuple[str, ...], str], ...]
    columns: tuple[Column, ...]
    filter: Filter | None = None

    @property
    def csv_columns(self) -> set[str]:
        return {field.name for field in self.fields}

    @property
    def sortable_keys(self) -> set[str]:
        return {column.key for column in self.columns if column.sortable}


# --------------------------------------------------------------------------- #
# The five dependency-free types
# --------------------------------------------------------------------------- #

_NAME_UNIQUE = (("name",), "duplicate name, already exists in this library")

OPERATION_TYPE = LibraryType(
    slug="operation-type",
    label="Op Types",
    model=OperationType,
    fields=(
        Field("name"),
        Field("description", required=False),
        Field("is_production", parse=parse_bool),
    ),
    unique_keys=(_NAME_UNIQUE,),
    columns=(
        Column("name", "Name"),
        Column("description", "Description"),
        Column("is_production", "Production", kind="bool"),
    ),
    filter=Filter(
        param="is_production",
        label="Production",
        options=(
            FilterOption("", "All"),
            FilterOption("true", "Production", Q(is_production=True)),
            FilterOption("false", "Non-production", Q(is_production=False)),
        ),
    ),
)

UNIT_TYPE = LibraryType(
    slug="unit-type",
    label="Unit Types",
    model=UnitType,
    fields=(Field("name"), Field("abbreviation")),
    unique_keys=(
        _NAME_UNIQUE,
        (("abbreviation",), "duplicate abbreviation, already exists in this library"),
    ),
    columns=(Column("name", "Name"), Column("abbreviation", "Abbreviation")),
)

STATION_CATEGORY = LibraryType(
    slug="station-category",
    label="Station Categories",
    model=StationCategory,
    fields=(Field("name"), Field("colour")),
    unique_keys=(
        _NAME_UNIQUE,
        (("colour",), "duplicate colour, already exists in this library"),
    ),
    columns=(Column("name", "Name"), Column("colour", "Colour", kind="colour")),
)

MATERIAL_CATEGORY = LibraryType(
    slug="material-category",
    label="Material Categories",
    model=MaterialCategory,
    fields=(Field("name"),),
    unique_keys=(_NAME_UNIQUE,),
    columns=(Column("name", "Name"),),
)

SHIFT_DEFINITION = LibraryType(
    slug="shift-definition",
    label="Shift Definitions",
    model=ShiftDefinition,
    fields=(
        Field("name"),
        Field("start_time", parse=parse_time),
        Field("end_time", parse=parse_time),
        Field("days", parse=parse_days, empty=None),
    ),
    unique_keys=(
        _NAME_UNIQUE,
        (
            ("start_time", "end_time", "days"),
            "duplicate shift window — same start, end and days already exists",
        ),
    ),
    columns=(
        Column("name", "Name"),
        Column("start_time", "Start", kind="time"),
        Column("end_time", "End", kind="time"),
        Column("days", "Days", kind="days"),
    ),
)


# Display order on the admin Libraries tab (mockup order, minus the C2 types).
DISPLAY_LIBRARY_TYPES: tuple[LibraryType, ...] = (
    OPERATION_TYPE,
    STATION_CATEGORY,
    MATERIAL_CATEGORY,
    SHIFT_DEFINITION,
    UNIT_TYPE,
)

LIBRARY_TYPES: dict[str, LibraryType] = {lt.slug: lt for lt in DISPLAY_LIBRARY_TYPES}


def get_library_type(slug: str) -> LibraryType:
    """Return the config for ``slug`` or raise ``KeyError`` (view maps to 404)."""
    return LIBRARY_TYPES[slug]
