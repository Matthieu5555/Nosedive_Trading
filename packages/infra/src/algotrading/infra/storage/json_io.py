"""JSON (de)serialization for raw events — a portable, reviewable sidecar to the Parquet store.

Parquet is the canonical raw layer; this codec exists for small, committed, redistributable
samples (a real market-data slice or a synthetic fixture) that must reconstruct offline, with no
broker connection and no Parquet partition on disk. Decimals round-trip exactly via a ``__dec__``
wrapper; timestamps are ISO-8601. The decoded events feed ``reconstruct_day`` like any other source.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from .events import RawMarketEvent


def _encode_value(value: Decimal | str | None) -> object:
    return {"__dec__": str(value)} if isinstance(value, Decimal) else value


def _decode_value(value: object) -> Decimal | str | None:
    if isinstance(value, dict) and "__dec__" in value:
        return Decimal(value["__dec__"])
    if value is None or isinstance(value, str):
        return value  # categorical / absent
    raise ValueError(f"unexpected encoded field value {value!r}")


def events_to_json(events: Sequence[RawMarketEvent]) -> str:
    """Serialize raw events to a stable JSON array (exact Decimals, ISO timestamps)."""
    rows = [
        {
            "collector_session_id": e.collector_session_id,
            "event_id": e.event_id,
            "receipt_ts": e.receipt_ts.isoformat(),
            "instrument_key": e.instrument_key,
            "field_name": e.field_name,
            "field_value": _encode_value(e.field_value),
            "underlying": e.underlying,
            "provider": e.provider,
            "exchange_ts": e.exchange_ts.isoformat() if e.exchange_ts else None,
            "contract_id_broker": e.contract_id_broker,
        }
        for e in events
    ]
    return json.dumps(rows, indent=2, sort_keys=True)


def events_from_json(text: str) -> list[RawMarketEvent]:
    """Parse the array written by :func:`events_to_json` back into ``RawMarketEvent`` instances."""
    return [
        RawMarketEvent(
            collector_session_id=row["collector_session_id"],
            event_id=row["event_id"],
            receipt_ts=datetime.fromisoformat(row["receipt_ts"]),
            instrument_key=row["instrument_key"],
            field_name=row["field_name"],
            field_value=_decode_value(row["field_value"]),
            underlying=row["underlying"],
            provider=row.get("provider", "DERIBIT"),
            exchange_ts=(
                datetime.fromisoformat(row["exchange_ts"]) if row.get("exchange_ts") else None
            ),
            contract_id_broker=row.get("contract_id_broker"),
        )
        for row in json.loads(text)
    ]
