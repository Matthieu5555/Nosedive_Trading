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
) -> tuple[RawMarketEvent, ...]:
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
            )
        )
    return tuple(events)
