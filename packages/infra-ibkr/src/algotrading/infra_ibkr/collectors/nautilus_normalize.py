"""Normalize Nautilus market-data ticks into our immutable ``RawMarketEvent``.

ADR 0023/0025: IBKR rides Nautilus's InteractiveBrokers adapter, which delivers
``QuoteTick``/``TradeTick`` for subscribed instruments. This module is the seam that
turns those into our system-of-record ``RawMarketEvent`` rows — one row per observed
field, content-addressed by :func:`content_event_id` so a re-delivered tick (same
``sequence``) is written exactly once. No broker SDK type crosses out of here; only
Nautilus's broker-agnostic tick types come in, and only ``RawMarketEvent`` goes out.

These functions are pure and use only Nautilus's *base* model types, so they are fully
exercised in CI without the ``ibkr`` extra or a TWS Gateway.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from algotrading.infra.contracts import RawMarketEvent, content_event_id
from nautilus_trader.model.data import QuoteTick, TradeTick

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _to_datetime(nanos: int) -> datetime:
    """Unix nanoseconds (Nautilus ``ts_event``/``ts_init``) → UTC datetime."""
    return _EPOCH + timedelta(microseconds=nanos // 1000)


def _event(
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    field_name: str,
    value: float,
    sequence: int,
    exchange_ts: datetime,
    receipt_ts: datetime,
) -> RawMarketEvent:
    return RawMarketEvent(
        session_id=session_id,
        event_id=content_event_id(instrument_key, field_name, sequence),
        instrument_key=instrument_key,
        exchange_ts=exchange_ts,
        receipt_ts=receipt_ts,
        canonical_ts=exchange_ts,
        field_name=field_name,
        value=value,
        trade_date=exchange_ts.date(),
        underlying=underlying,
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

    ``sequence`` is the feed's stable per-session ordinal for this update; bid and ask of
    the same tick share it but differ by ``field_name``, so they get distinct event ids,
    while a re-delivered tick at the same ``sequence`` reproduces the same ids (idempotent).
    ``exchange_ts`` is the tick's ``ts_event``; ``receipt_ts`` is ``ts_init``.
    """
    exchange_ts = _to_datetime(tick.ts_event)
    receipt_ts = _to_datetime(tick.ts_init)
    fields = (
        ("bid", float(tick.bid_price)),
        ("ask", float(tick.ask_price)),
        ("bid_size", float(tick.bid_size)),
        ("ask_size", float(tick.ask_size)),
    )
    return tuple(
        _event(
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
    exchange_ts = _to_datetime(tick.ts_event)
    receipt_ts = _to_datetime(tick.ts_init)
    fields = (
        ("last", float(tick.price)),
        ("last_size", float(tick.size)),
    )
    return tuple(
        _event(
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
