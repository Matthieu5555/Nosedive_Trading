from collections.abc import Mapping
from datetime import datetime

from algotrading.infra.contracts import RawMarketEvent

from .cp_rest_wire import SNAPSHOT_FIELD_TAGS, SnapshotRow
from .market_fields import (
    ASK,
    ASK_SIZE,
    BID,
    BID_SIZE,
    LAST,
    LAST_SIZE,
    VOLUME,
    raw_market_event,
)

REQUEST_FIELD_TAGS: tuple[str, ...] = SNAPSHOT_FIELD_TAGS

_FIELDS: tuple[tuple[str, str], ...] = (
    ("bid", BID),
    ("ask", ASK),
    ("bid_size", BID_SIZE),
    ("ask_size", ASK_SIZE),
    ("last", LAST),
    ("last_size", LAST_SIZE),
    ("volume", VOLUME),
)


def snapshot_to_events(
    row: Mapping[str, object] | SnapshotRow,
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    sequence: int,
    exchange_ts: datetime,
    receipt_ts: datetime,
    canonical_ts: datetime | None = None,
) -> tuple[RawMarketEvent, ...]:
    """One CP market-data row (snapshot or WS frame) → its fields as ``RawMarketEvent`` rows.

    Only recognized, present, parseable fields become events; absent or sentinel values are
    skipped (never emitted as a fake observation). ``sequence`` is the per-session ordinal that
    makes a re-delivered row idempotent. ``exchange_ts`` is the row's broker update time
    (CP ``_updated``); ``receipt_ts`` is when we received it; ``canonical_ts`` is the normalized
    ordering clock (defaults to ``exchange_ts``; the close capture passes the session-close instant
    so all marks order at the close while keeping their real broker ``exchange_ts``). Accepts either
    the raw mapping or an already-validated :class:`SnapshotRow` (pre-parsing callers avoid rework).
    """
    parsed = row if isinstance(row, SnapshotRow) else SnapshotRow.model_validate(row)
    events: list[RawMarketEvent] = []
    for attribute, field_name in _FIELDS:
        value: float | None = getattr(parsed, attribute)
        if value is None:
            continue
        events.append(
            raw_market_event(
                instrument_key=instrument_key,
                underlying=underlying,
                session_id=session_id,
                field_name=field_name,
                value=value,
                sequence=sequence,
                exchange_ts=exchange_ts,
                receipt_ts=receipt_ts,
                canonical_ts=canonical_ts,
            )
        )
    return tuple(events)
