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
    fields = [
        pa.field(name, _arrow_type_for(annotation))
        for name, annotation in resolved_field_types(contract).items()
    ]
    return pa.schema(fields)
