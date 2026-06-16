from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import TypeAdapter

from .events import CollectorEvent

_EVENT_LIST: TypeAdapter[list[CollectorEvent]] = TypeAdapter(list[CollectorEvent])


def _encode_wire_value(value: object) -> object:
    if isinstance(value, Decimal):
        return {"__dec__": str(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unexpected value {value!r} in a collector-event sample")


def _decode_value(value: object) -> Decimal | str | None:
    if isinstance(value, dict) and "__dec__" in value:
        return Decimal(value["__dec__"])
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"unexpected encoded field value {value!r}")


def events_to_json(events: Sequence[CollectorEvent]) -> str:
    rows = _EVENT_LIST.dump_python(list(events))
    return json.dumps(rows, indent=2, sort_keys=True, default=_encode_wire_value)


def events_from_json(text: str) -> list[CollectorEvent]:
    rows = json.loads(text)
    for row in rows:
        row["field_value"] = _decode_value(row["field_value"])
    return _EVENT_LIST.validate_python(rows)
