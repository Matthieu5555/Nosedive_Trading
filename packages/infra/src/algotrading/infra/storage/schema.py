"""Derive the Parquet (Arrow) schema for a table from its contract.

There is exactly one schema per table and both the live and the replay write
paths use it, which is what makes "live and replay land in identical schemas" a
fact rather than a wish. Numbers are real numeric Arrow types, never strings;
timestamps are UTC-stamped; nested objects are JSON strings. The schema is
derived from the contract's type hints so it can never silently disagree with the
dataclass.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import cast

import pyarrow as pa
from algotrading.infra.contracts.registry import resolved_field_types, unwrap_optional

from .serialization import _is_json_field

_SCALAR_ARROW_TYPES = {
    str: pa.string(),
    bool: pa.bool_(),
    int: pa.int64(),
    float: pa.float64(),
}


def _arrow_type_for(annotation: object) -> pa.DataType:
    inner, _ = unwrap_optional(annotation)
    if _is_json_field(inner):
        return pa.string()
    if inner is datetime:
        return pa.timestamp("us", tz="UTC")
    if inner is date:
        return pa.date32()
    arrow_type = _SCALAR_ARROW_TYPES.get(cast(type, inner))
    if arrow_type is None:
        raise TypeError(f"no Arrow type mapping for field annotation {annotation!r}")
    return arrow_type


def arrow_schema(contract: type) -> pa.Schema:
    """Return the Arrow schema for a contract, in declared field order."""
    fields = [
        pa.field(name, _arrow_type_for(annotation))
        for name, annotation in resolved_field_types(contract).items()
    ]
    return pa.schema(fields)
