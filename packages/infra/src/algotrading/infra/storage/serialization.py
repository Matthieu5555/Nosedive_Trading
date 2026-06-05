"""Turn a typed contract into a flat storage row, and back again, losslessly.

Storage rows are deliberately flat: every column is a scalar, a date, a timestamp,
or — for the nested pieces (the instrument key, the provenance stamp, the
diagnostic bundles, tuples like ``flags``) — a single JSON string. Keeping the
nested bits as JSON columns means the Parquet schema stays simple and explicit,
which is exactly what the roadmap asks for, while still round-tripping back into
the original frozen objects.

The codec is driven entirely by the dataclass type hints, so there is no
hand-maintained per-field mapping to fall out of sync with the contracts.

Reading enforces the schema-evolution rule rather than just trusting it: a value
that is absent or null is accepted only for an ``Optional`` field (it becomes
``None``); a missing *required* field raises :class:`SchemaCompatibilityError`
instead of silently constructing an invalid contract instance. This applies at
both levels — top-level columns and the fields of a nested JSON bundle.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from typing import Any, cast, get_args, get_origin

from algotrading.infra.contracts.registry import resolved_field_types, unwrap_optional

from .errors import SchemaCompatibilityError


def _is_dataclass_type(annotation: object) -> bool:
    return dataclasses.is_dataclass(annotation) and isinstance(annotation, type)


def _is_json_field(inner: object) -> bool:
    """A field is stored as JSON when it is a nested dataclass or a tuple/list."""
    return _is_dataclass_type(inner) or get_origin(inner) in (tuple, list)


def _jsonify(value: Any, annotation: object) -> Any:
    """Convert a value into JSON-safe primitives, preserving dates/datetimes."""
    inner, _ = unwrap_optional(annotation)
    if value is None:
        return None
    if _is_dataclass_type(inner):
        field_types = resolved_field_types(inner)  # type: ignore[arg-type]
        return {
            name: _jsonify(getattr(value, name), field_types[name]) for name in field_types
        }
    if inner is datetime:
        return value.astimezone(UTC).isoformat()
    if inner is date:
        return value.isoformat()
    if get_origin(inner) in (tuple, list):
        (element_type,) = (get_args(inner) or (Any,))[:1]
        return [_jsonify(item, element_type) for item in value]
    return value


def _checked_raw(owner: type, name: str, mapping: dict[str, Any], annotation: object) -> Any:
    """Fetch one field's raw value from a stored mapping, enforcing the null rule.

    A value that is absent or null is fine for an ``Optional`` field (returned as
    ``None``) and refused for a required one — the single place the additive-
    nullable schema-evolution rule is enforced, shared by top-level columns and
    nested JSON bundles.
    """
    _, is_optional = unwrap_optional(annotation)
    raw = mapping.get(name)
    if raw is None and not is_optional:
        raise SchemaCompatibilityError(owner, name)
    return raw


def _unjsonify(raw: Any, annotation: object) -> Any:
    """Rebuild a typed value from JSON-safe primitives."""
    inner, _ = unwrap_optional(annotation)
    if raw is None:
        return None
    if _is_dataclass_type(inner):
        cls = cast(type, inner)
        field_types = resolved_field_types(cls)
        return cls(
            **{
                name: _unjsonify(_checked_raw(cls, name, raw, typ), typ)
                for name, typ in field_types.items()
            }
        )
    if inner is datetime:
        return datetime.fromisoformat(raw)
    if inner is date:
        return date.fromisoformat(raw)
    if get_origin(inner) in (tuple, list):
        (element_type,) = (get_args(inner) or (Any,))[:1]
        return tuple(_unjsonify(item, element_type) for item in raw)
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
                else json.dumps(_jsonify(value, annotation), sort_keys=True, separators=(",", ":"))
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
            kwargs[name] = None if raw is None else _unjsonify(json.loads(raw), annotation)
        else:
            kwargs[name] = _coerce_scalar(raw, inner)
    return contract(**kwargs)
