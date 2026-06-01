"""Turn a typed contract into a flat storage row, and back again, losslessly.

Storage rows are deliberately flat: every column is a scalar, a date, a timestamp,
or — for the nested pieces (the instrument key, the provenance stamp, the
diagnostic bundles, tuples like ``flags``) — a single JSON string. Keeping the
nested bits as JSON columns means the Parquet schema stays simple and explicit,
which is exactly what the roadmap asks for, while still round-tripping back into
the original frozen objects.

The codec is driven entirely by the dataclass type hints, so there is no
hand-maintained per-field mapping to fall out of sync with the contracts.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from typing import Any, cast, get_args, get_origin

from contracts.registry import resolved_field_types, unwrap_optional


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


def _unjsonify(raw: Any, annotation: object) -> Any:
    """Rebuild a typed value from JSON-safe primitives."""
    inner, _ = unwrap_optional(annotation)
    if raw is None:
        return None
    if _is_dataclass_type(inner):
        cls = cast(type, inner)
        field_types = resolved_field_types(cls)
        return cls(**{name: _unjsonify(raw[name], typ) for name, typ in field_types.items()})
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

    Columns absent from ``row`` (an older partition written before a nullable
    column was added) are filled with ``None``, so old data stays readable.
    """
    kwargs: dict[str, Any] = {}
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        raw = row.get(name)
        if _is_json_field(inner):
            kwargs[name] = None if raw is None else _unjsonify(json.loads(raw), annotation)
        else:
            kwargs[name] = _coerce_scalar(raw, inner)
    return contract(**kwargs)
