"""Turn a typed contract into a flat storage row, and back again, losslessly.

Storage rows are deliberately flat: every column is a scalar, a date, a timestamp,
or — for the nested pieces (the instrument key, the provenance stamp, the
diagnostic bundles, tuples like ``flags``) — a single JSON string. Keeping the
nested bits as JSON columns means the Parquet schema stays simple and explicit,
which is exactly what the roadmap asks for, while still round-tripping back into
the original frozen objects.

The codec is driven entirely by the dataclass type hints — pydantic
``TypeAdapter``s do the structural work (recursing into nested dataclasses and
tuples on both sides), so there is no hand-maintained per-field mapping to fall
out of sync with the contracts. The persisted byte conventions stay exactly the
pre-pydantic ones, pinned by the golden suite: JSON columns are compact
sorted-key JSON, nested datetimes are normalized to UTC ISO-8601, dates are ISO.

Reading enforces the schema-evolution rule rather than just trusting it: a value
that is absent or null is accepted only for an ``Optional`` field (it becomes
``None``); a missing *required* field raises :class:`SchemaCompatibilityError`
instead of silently constructing an invalid contract instance. This applies at
both levels — top-level columns and the fields of a nested JSON bundle, where the
rule is pydantic's missing/null validation mapped back onto the storage error.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from typing import Any, cast, get_args, get_origin

from algotrading.infra.contracts.registry import resolved_field_types, unwrap_optional
from pydantic import TypeAdapter, ValidationError

from .errors import SchemaCompatibilityError

# The persisted JSON-column byte convention: sorted keys, no whitespace. Changing
# this changes every stored JSON column and the canonical-hash anchor — don't.
_COLUMN_DUMP_ARGS: dict[str, Any] = {"sort_keys": True, "separators": (",", ":")}


def _is_dataclass_type(annotation: object) -> bool:
    return dataclasses.is_dataclass(annotation) and isinstance(annotation, type)


def _is_json_field(inner: object) -> bool:
    """A field is stored as JSON when it is a nested dataclass or a tuple/list."""
    return _is_dataclass_type(inner) or get_origin(inner) in (tuple, list)


# One TypeAdapter per distinct annotation, built lazily: the adapter owns the
# recursive encode/decode structure the old reflective codec hand-rolled.
_ADAPTERS: dict[object, TypeAdapter[Any]] = {}


def _adapter_for(annotation: object) -> TypeAdapter[Any]:
    try:
        return _ADAPTERS[annotation]
    except KeyError:
        adapter: TypeAdapter[Any] = TypeAdapter(annotation)
        _ADAPTERS[annotation] = adapter
        return adapter


def _json_temporal(value: object) -> str:
    """The pinned byte form of the temporal types inside a JSON column."""
    # datetime first — it is a date subclass, and it alone is normalized to UTC.
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"value {value!r} cannot be stored in a contract JSON column")


def _owner_and_field(
    contract: type, name: str, annotation: object, loc: tuple[Any, ...]
) -> tuple[type, str]:
    """Resolve a pydantic error location to the dataclass and field that own it.

    Walks the error's ``loc`` path through the column's type annotation so a
    failure deep inside a nested bundle (or a tuple element) is reported against
    the bundle class, exactly as the pre-pydantic codec did. Falls back to the
    contract and column name when the path stops short of a dataclass field
    (e.g. a whole tuple element null).
    """
    owner, field_name = contract, name
    current: object = annotation
    for part in loc:
        inner, _ = unwrap_optional(current)
        if isinstance(part, str):
            if not _is_dataclass_type(inner):
                break
            owner = cast(type, inner)
            field_name = part
            current = resolved_field_types(owner)[part]
        else:  # a tuple/list index: descend into the element type
            current = (get_args(inner) or (Any,))[0]
    return owner, field_name


def _decode_json_column(contract: type, name: str, annotation: object, raw: str) -> Any:
    """Rebuild a JSON column's typed value, enforcing the additive-nullable rule."""
    inner, _ = unwrap_optional(annotation)
    try:
        return _adapter_for(inner).validate_python(json.loads(raw))
    except ValidationError as exc:
        for error in exc.errors():
            # absent-or-null on a required field is the schema-evolution breach;
            # anything else (real type drift) surfaces as the pydantic error.
            if error["type"] == "missing" or error.get("input") is None:
                owner, field_name = _owner_and_field(
                    contract, name, annotation, tuple(error["loc"])
                )
                raise SchemaCompatibilityError(owner, field_name) from None
        raise


def _checked_raw(owner: type, name: str, mapping: dict[str, Any], annotation: object) -> Any:
    """Fetch one column's raw value from a stored row, enforcing the null rule.

    A value that is absent or null is fine for an ``Optional`` column (returned as
    ``None``) and refused for a required one — the top-level half of the additive-
    nullable schema-evolution rule (pydantic owns the nested half).
    """
    _, is_optional = unwrap_optional(annotation)
    raw = mapping.get(name)
    if raw is None and not is_optional:
        raise SchemaCompatibilityError(owner, name)
    return raw


def _coerce_scalar(raw: Any, inner: object) -> Any:
    """Normalize a scalar read back from storage to the contract's expectation."""
    if raw is None:
        return None
    if inner is datetime and isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    if inner is date and isinstance(raw, datetime):
        return raw.date()
    return raw


def to_row(contract: type, record: object) -> dict[str, Any]:
    """Flatten a contract instance into a storage row (scalars + JSON columns)."""
    row: dict[str, Any] = {}
    for name, annotation in resolved_field_types(contract).items():
        value = getattr(record, name)
        inner, _ = unwrap_optional(annotation)
        if _is_json_field(inner):
            row[name] = (
                None
                if value is None
                else json.dumps(
                    _adapter_for(inner).dump_python(value),
                    default=_json_temporal,
                    **_COLUMN_DUMP_ARGS,
                )
            )
        else:
            row[name] = value
    return row


def from_row(contract: type, row: dict[str, Any]) -> object:
    """Rebuild a contract instance from a storage row.

    A column that is absent or null fills its field with ``None`` only when the
    field is ``Optional`` — the additive-nullable case, where an older partition
    predates a new nullable column. A missing *required* field raises
    :class:`SchemaCompatibilityError` rather than constructing an invalid instance.
    """
    kwargs: dict[str, Any] = {}
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        raw = _checked_raw(contract, name, row, annotation)
        if _is_json_field(inner):
            kwargs[name] = (
                None if raw is None else _decode_json_column(contract, name, annotation, raw)
            )
        else:
            kwargs[name] = _coerce_scalar(raw, inner)
    return contract(**kwargs)
