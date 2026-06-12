"""JSON (de)serialization for raw events — a portable, reviewable sidecar to the Parquet store.

Parquet is the canonical raw layer; this codec exists for small, committed, redistributable
samples (a real market-data slice or a synthetic fixture) that must reconstruct offline, with no
broker connection and no Parquet partition on disk. Decimals round-trip exactly via a ``__dec__``
wrapper; timestamps are ISO-8601. The decoded events feed ``reconstruct_day`` like any other source.

A pydantic ``TypeAdapter`` over :class:`CollectorEvent` owns the field structure on both
sides (so a new event field never needs a hand-edited field list here); the two bespoke
wire rules stay explicit because they *are* the persisted format: the ``__dec__`` Decimal
wrapper, and the read-side ``provider`` default (the dataclass default, applied by
validation when an old sample predates the provider field).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import TypeAdapter

from .events import CollectorEvent

_EVENT_LIST: TypeAdapter[list[CollectorEvent]] = TypeAdapter(list[CollectorEvent])


def _encode_wire_value(value: object) -> object:
    """The pinned byte form of the non-JSON-native types on the wire."""
    if isinstance(value, Decimal):
        return {"__dec__": str(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unexpected value {value!r} in a collector-event sample")


def _decode_value(value: object) -> Decimal | str | None:
    if isinstance(value, dict) and "__dec__" in value:
        return Decimal(value["__dec__"])
    if value is None or isinstance(value, str):
        return value  # categorical / absent
    raise ValueError(f"unexpected encoded field value {value!r}")


def events_to_json(events: Sequence[CollectorEvent]) -> str:
    """Serialize raw events to a stable JSON array (exact Decimals, ISO timestamps)."""
    rows = _EVENT_LIST.dump_python(list(events))
    return json.dumps(rows, indent=2, sort_keys=True, default=_encode_wire_value)


def events_from_json(text: str) -> list[CollectorEvent]:
    """Parse the array written by :func:`events_to_json` back into ``CollectorEvent`` instances."""
    rows = json.loads(text)
    for row in rows:
        row["field_value"] = _decode_value(row["field_value"])
    return _EVENT_LIST.validate_python(rows)
