from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from typing import Any, cast, get_args, get_origin

from algotrading.infra.contracts.registry import resolved_field_types, unwrap_optional
from pydantic import TypeAdapter, ValidationError

from .errors import SchemaCompatibilityError

_COLUMN_DUMP_ARGS: dict[str, Any] = {"sort_keys": True, "separators": (",", ":")}


def _is_dataclass_type(annotation: object) -> bool:
    return dataclasses.is_dataclass(annotation) and isinstance(annotation, type)


def _is_json_field(inner: object) -> bool:
    return _is_dataclass_type(inner) or get_origin(inner) in (tuple, list)


_ADAPTERS: dict[object, TypeAdapter[Any]] = {}


def _adapter_for(annotation: object) -> TypeAdapter[Any]:
    try:
        return _ADAPTERS[annotation]
    except KeyError:
        adapter: TypeAdapter[Any] = TypeAdapter(annotation)
        _ADAPTERS[annotation] = adapter
        return adapter


def _json_temporal(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"value {value!r} cannot be stored in a contract JSON column")


def _owner_and_field(
    contract: type, name: str, annotation: object, loc: tuple[Any, ...]
) -> tuple[type, str]:
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
        else:
            current = (get_args(inner) or (Any,))[0]
    return owner, field_name


def _decode_json_column(contract: type, name: str, annotation: object, raw: str) -> Any:
    inner, _ = unwrap_optional(annotation)
    try:
        return _adapter_for(inner).validate_python(json.loads(raw))
    except ValidationError as exc:
        for error in exc.errors():
            if error["type"] == "missing" or error.get("input") is None:
                owner, field_name = _owner_and_field(
                    contract, name, annotation, tuple(error["loc"])
                )
                raise SchemaCompatibilityError(owner, field_name) from None
        raise


def _checked_raw(owner: type, name: str, mapping: dict[str, Any], annotation: object) -> Any:
    _, is_optional = unwrap_optional(annotation)
    raw = mapping.get(name)
    if raw is None and not is_optional:
        raise SchemaCompatibilityError(owner, name)
    return raw


def _coerce_scalar(raw: Any, inner: object) -> Any:
    if raw is None:
        return None
    if inner is datetime and isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    if inner is date and isinstance(raw, datetime):
        return raw.date()
    return raw


def to_row(contract: type, record: object) -> dict[str, Any]:
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
