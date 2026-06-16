from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from algotrading.infra.contracts import InstrumentKey, RawMarketEvent

from .library import make_option, make_underlying

SNAPSHOT_TS = datetime(2026, 5, 29, 15, 30, 0, tzinfo=UTC)
STALE_THRESHOLD_SECONDS = 30.0

SESSION_ID = "sess-snapshot"
UNDERLYING = make_underlying("AAPL")
OPTION = make_option("AAPL", 100.0, "C", date(2026, 6, 19))


def event(
    instrument: InstrumentKey,
    field_name: str,
    value: float,
    *,
    ts: datetime,
    session_id: str = SESSION_ID,
    event_id: str | None = None,
) -> RawMarketEvent:
    key = instrument.canonical()
    resolved_id = event_id if event_id is not None else f"{field_name}@{ts.isoformat()}"
    return RawMarketEvent(
        session_id=session_id,
        event_id=resolved_id,
        instrument_key=key,
        exchange_ts=ts,
        receipt_ts=ts,
        canonical_ts=ts,
        field_name=field_name,
        value=value,
        trade_date=ts.date(),
        underlying=instrument.underlying_symbol,
    )


def quote_events(
    instrument: InstrumentKey,
    *,
    bid: float | None = None,
    ask: float | None = None,
    last: float | None = None,
    ts: datetime = SNAPSHOT_TS,
    session_id: str = SESSION_ID,
) -> tuple[RawMarketEvent, ...]:
    fields = (("bid", bid), ("ask", ask), ("last", last))
    return tuple(
        event(instrument, name, value, ts=ts, session_id=session_id)
        for name, value in fields
        if value is not None
    )


def boundary_bid_events() -> tuple[RawMarketEvent, ...]:
    return (
        event(UNDERLYING, "bid", 190.0, ts=SNAPSHOT_TS - timedelta(seconds=5), event_id="b-early"),
        event(UNDERLYING, "bid", 190.5, ts=SNAPSHOT_TS, event_id="b-at"),
        event(UNDERLYING, "bid", 191.0, ts=SNAPSHOT_TS + timedelta(seconds=1), event_id="b-after"),
    )


def threshold_straddle_events() -> tuple[RawMarketEvent, ...]:
    at_threshold = SNAPSHOT_TS - timedelta(seconds=STALE_THRESHOLD_SECONDS)
    return quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5, ts=at_threshold)


def crossed_then_last_events() -> tuple[RawMarketEvent, ...]:
    return quote_events(UNDERLYING, bid=190.6, ask=190.4, last=190.5)


def single_last_event() -> tuple[RawMarketEvent, ...]:
    return (event(UNDERLYING, "last", 190.5, ts=SNAPSHOT_TS),)


def single_bid_event() -> tuple[RawMarketEvent, ...]:
    return (event(UNDERLYING, "bid", 190.4, ts=SNAPSHOT_TS),)
