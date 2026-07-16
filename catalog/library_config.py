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
from decimal import Decimal

from django.db import models
from django.db.models import Q

from catalog.models import (
    MaterialCategory,
    OperationType,
    ShiftDefinition,
    Station,
    StationCategory,
    UnitType,
    WorkshopRole,
)
from catalog.seeds import ADMIN_ROLE_NAME, UNDEFINED_NAME


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


def parse_station_status(raw: str) -> str:
    """Validate a Station status against the model's choices (optional column).

    Blank is handled upstream by the field's ``empty`` default (active); a
    non-blank value must be one of the state-machine states or the row is skipped.
    """
    value = raw.strip().lower()
    if value in set(Station.Status.values):
        return value
    allowed = ", ".join(Station.Status.values)
    raise RowError(f'invalid status "{raw}" (expected one of: {allowed})')


def parse_lot_sizes(raw: str) -> list:
    """Parse ``;``-separated lot quantities into a JSON list of numbers.

    Storage only in this slice — purchase-order lot-quantity validation is Slice E.
    Each token must be a non-negative number; whole numbers are stored as ints so
    the JSON stays clean (``10`` not ``10.0``).
    """
    tokens = [tok.strip() for tok in raw.split(";") if tok.strip()]
    if not tokens:
        raise RowError("no lot sizes given")
    sizes = []
    for tok in tokens:
        try:
            number = Decimal(tok)
        except (ArithmeticError, ValueError):
            raise RowError(f'invalid lot size "{tok}" (expected a number)') from None
        if number < 0:
            raise RowError(f'invalid lot size "{tok}" (must not be negative)')
        sizes.append(int(number) if number == number.to_integral_value() else float(number))
    return sizes


def parse_decimal(raw: str) -> Decimal:
    """Parse a non-negative decimal quantity (current_stock, min_threshold)."""
    try:
        number = Decimal(raw.strip())
    except (ArithmeticError, ValueError):
        raise RowError(f'invalid number "{raw}"') from None
    if number < 0:
        raise RowError(f'invalid number "{raw}" (must not be negative)')
    return number


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
class RefField:
    """A CSV column holding a name that resolves to a single related row (FK).

    Resolution is scoped to the importing workshop (D-126) — names collide across
    workshops. ``target_label`` is the human name of the library it resolves
    against, for the skip reason (mirrors the mockup's
    ``category "Veneer" not found in Material Categories``).
    """

    name: str
    target: type[models.Model]
    target_label: str
    required: bool = True


@dataclass(frozen=True)
class M2MField:
    """A CSV column holding a ``;``-separated list of names resolving to rows (M2M).

    Zero names (blank column) is allowed. Any single name that cannot be resolved
    within the workshop skips the whole row and is reported, naming the failing
    element (``singular``, e.g. "operation"). The relation is set after the row is
    created.
    """

    name: str
    target: type[models.Model]
    target_label: str
    singular: str
    delimiter: str = ";"


@dataclass(frozen=True)
class Column:
    """One column of the read-only table. ``kind`` drives cell rendering."""

    key: str
    label: str
    kind: str = "text"  # text | bool | colour | time | days | list | ref | status
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
    # Scalar columns mapped 1:1 to a model field of the same name.
    fields: tuple[Field, ...]
    # Each entry: the field-set that must be jointly unique, and the reason to
    # report when a row collides on it. Mirrors the model's DB constraints.
    unique_keys: tuple[tuple[tuple[str, ...], str], ...]
    # Name-resolving columns (C2). FK columns resolve to a single row; M2M columns
    # resolve a ;-separated list. Both resolve within the workshop, skip+report on
    # failure. Empty for the C1 dependency-free types.
    ref_fields: tuple[RefField, ...] = ()
    m2m_fields: tuple[M2MField, ...] = ()
    # System-reserved names that may never be imported (matched case-insensitively,
    # against the global sentinels — WorkshopRole's "Admin"/"undefined").
    reserved_names: frozenset[str] = frozenset()
    reserved_reason: str = ""
    # Read-only-table config, used only by the generic table view. A type with a
    # dedicated view (Station) leaves these empty and renders its own template.
    columns: tuple[Column, ...] = ()
    filter: Filter | None = None
    page_size: int = 20

    @property
    def csv_columns(self) -> set[str]:
        return {
            *(f.name for f in self.fields),
            *(r.name for r in self.ref_fields),
            *(m.name for m in self.m2m_fields),
        }

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


# --------------------------------------------------------------------------- #
# The name-resolving types (C2)
# --------------------------------------------------------------------------- #

# WorkshopRole is a Libraries-tab card like the five above, so it carries table
# config and renders through the generic view. Its default_clearances resolve
# against OperationType by name; the reserved guard blocks the seeded system roles.
WORKSHOP_ROLE = LibraryType(
    slug="workshop-role",
    label="Workshop Roles",
    model=WorkshopRole,
    fields=(Field("name"), Field("description", required=False)),
    unique_keys=(_NAME_UNIQUE,),
    m2m_fields=(
        M2MField(
            name="default_clearances",
            target=OperationType,
            target_label="Op Types",
            singular="clearance",
        ),
    ),
    reserved_names=frozenset({ADMIN_ROLE_NAME.lower(), UNDEFINED_NAME.lower()}),
    reserved_reason="reserved system role, cannot be imported",
    columns=(
        Column("name", "Name"),
        Column("description", "Description"),
        Column("default_clearances", "Default clearances", kind="list", sortable=False),
    ),
    page_size=50,
)

# Station renders on its own admin tab via a dedicated view (two combinable
# filters + grouped list column), so it carries no generic-table config — only
# what the import engine needs. category resolves against StationCategory;
# supported_operations against OperationType; the ST-NNN code is assigned in save().
STATION = LibraryType(
    slug="station",
    label="Stations",
    model=Station,
    fields=(
        Field("name"),
        Field(
            "status",
            required=False,
            parse=parse_station_status,
            empty=Station.Status.ACTIVE,
        ),
    ),
    unique_keys=(_NAME_UNIQUE,),
    ref_fields=(
        RefField(name="category", target=StationCategory, target_label="Station Categories"),
    ),
    m2m_fields=(
        M2MField(
            name="supported_operations",
            target=OperationType,
            target_label="Op Types",
            singular="operation",
        ),
    ),
)


# Display order on the admin Libraries tab (mockup order): the six card types.
# Station and Material are not cards — they render on their own tabs.
DISPLAY_LIBRARY_TYPES: tuple[LibraryType, ...] = (
    OPERATION_TYPE,
    STATION_CATEGORY,
    MATERIAL_CATEGORY,
    SHIFT_DEFINITION,
    WORKSHOP_ROLE,
    UNIT_TYPE,
)

# Types importable through the engine but not shown as Libraries-tab cards.
_ENGINE_ONLY_TYPES: tuple[LibraryType, ...] = (STATION,)

LIBRARY_TYPES: dict[str, LibraryType] = {
    lt.slug: lt for lt in (*DISPLAY_LIBRARY_TYPES, *_ENGINE_ONLY_TYPES)
}


def get_library_type(slug: str) -> LibraryType:
    """Return the config for ``slug`` or raise ``KeyError`` (view maps to 404)."""
    return LIBRARY_TYPES[slug]
