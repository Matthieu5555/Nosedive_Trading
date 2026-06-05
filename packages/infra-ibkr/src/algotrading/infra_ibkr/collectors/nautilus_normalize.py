"""Normalize Nautilus market-data ticks into our immutable ``RawMarketEvent``.

ADR 0023/0025: IBKR rides Nautilus's InteractiveBrokers adapter, which delivers
``QuoteTick``/``TradeTick`` for subscribed instruments. This module maps those onto our
system-of-record ``RawMarketEvent`` rows — one row per observed field, content-addressed so a
re-delivered tick (same ``sequence``) is written exactly once. No broker SDK type crosses out of
here; only Nautilus's broker-agnostic tick types come in, and only ``RawMarketEvent`` goes out.

Events are built through the shared :mod:`.market_fields` helper, so this path and the Client
Portal REST path (``cp_rest_normalize``) agree on field names and event construction by
construction (ADR 0024's equivalence bar). Pure, base-Nautilus-types only — fully exercised in CI.
"""

from collections.abc import Sequence

from algotrading.infra.contracts import RawMarketEvent
from nautilus_trader.model.data import QuoteTick, TradeTick

from .market_fields import (
    ASK,
    ASK_SIZE,
    BID,
    BID_SIZE,
    LAST,
    LAST_SIZE,
    raw_market_event,
    to_datetime,
)


def quote_tick_to_events(
    tick: QuoteTick,
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    sequence: int,
) -> tuple[RawMarketEvent, ...]:
    """One Nautilus ``QuoteTick`` → its bid/ask (+sizes) as ``RawMarketEvent`` rows.

    ``sequence`` is the feed's stable per-session ordinal for this update; bid and ask of the same
    tick share it but differ by ``field_name``, so they get distinct event ids, while a re-delivered
    tick at the same ``sequence`` reproduces the same ids (idempotent). ``exchange_ts`` is the
    tick's ``ts_event``; ``receipt_ts`` is ``ts_init``.
    """
    exchange_ts = to_datetime(tick.ts_event)
    receipt_ts = to_datetime(tick.ts_init)
    fields = (
        (BID, float(tick.bid_price)),
        (ASK, float(tick.ask_price)),
        (BID_SIZE, float(tick.bid_size)),
        (ASK_SIZE, float(tick.ask_size)),
    )
    return tuple(
        raw_market_event(
            instrument_key=instrument_key,
            underlying=underlying,
            session_id=session_id,
            field_name=name,
            value=value,
            sequence=sequence,
            exchange_ts=exchange_ts,
            receipt_ts=receipt_ts,
        )
        for name, value in fields
    )


def trade_tick_to_events(
    tick: TradeTick,
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    sequence: int,
) -> tuple[RawMarketEvent, ...]:
    """One Nautilus ``TradeTick`` → its last price + size as ``RawMarketEvent`` rows."""
    exchange_ts = to_datetime(tick.ts_event)
    receipt_ts = to_datetime(tick.ts_init)
    fields = (
        (LAST, float(tick.price)),
        (LAST_SIZE, float(tick.size)),
    )
    return tuple(
        raw_market_event(
            instrument_key=instrument_key,
            underlying=underlying,
            session_id=session_id,
            field_name=name,
            value=value,
            sequence=sequence,
            exchange_ts=exchange_ts,
            receipt_ts=receipt_ts,
        )
        for name, value in fields
    )


def quote_ticks_to_events(
    ticks: Sequence[QuoteTick],
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    first_sequence: int = 0,
) -> tuple[RawMarketEvent, ...]:
    """Normalize a run of quote ticks, assigning each a monotonic ``sequence``."""
    events: list[RawMarketEvent] = []
    for offset, tick in enumerate(ticks):
        events.extend(
            quote_tick_to_events(
                tick,
                instrument_key=instrument_key,
                underlying=underlying,
                session_id=session_id,
                sequence=first_sequence + offset,
            )
        )
    return tuple(events)
