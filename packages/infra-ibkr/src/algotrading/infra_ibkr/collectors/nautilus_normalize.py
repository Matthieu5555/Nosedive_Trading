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
