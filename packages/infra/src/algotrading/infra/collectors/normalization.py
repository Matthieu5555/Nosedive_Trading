from __future__ import annotations

import hashlib
from datetime import date

from algotrading.infra.connectivity import GapInterval
from algotrading.infra.contracts import RawMarketEvent

GAP_FIELD = "__gap__"

_ID_SEPARATOR = "\x1f"


def meta_event_id(instrument_key: str, field_name: str, token: str) -> str:
    payload = _ID_SEPARATOR.join((instrument_key, field_name, token))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_gap_event(
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    trade_date: date,
    gap: GapInterval,
) -> RawMarketEvent:
    ended = gap.ended_at
    return RawMarketEvent(
        session_id=session_id,
        event_id=meta_event_id(instrument_key, GAP_FIELD, ended.isoformat()),
        instrument_key=instrument_key,
        exchange_ts=ended,
        receipt_ts=ended,
        canonical_ts=ended,
        field_name=GAP_FIELD,
        value=gap.duration_seconds(),
        trade_date=trade_date,
        underlying=underlying,
    )
