from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from algotrading.infra.contracts import RawMarketEvent
from algotrading.infra.storage import ParquetStore

from .collector import FeedFault
from .normalize import BrokerTick, is_observation

_RAW_MARKET_EVENTS = "raw_market_events"


def replay_day(
    store: ParquetStore, trade_date: date, *, underlying: str | None = None
) -> list[RawMarketEvent]:
    if underlying is not None:
        events = store.read(_RAW_MARKET_EVENTS, trade_date=trade_date, underlying=underlying)
    else:
        events = store.read(_RAW_MARKET_EVENTS, trade_date=trade_date)
    return sorted(events, key=lambda event: (event.canonical_ts, event.event_id))


def next_sequence(
    counters: dict[tuple[str, str], int], instrument_key: str, field_name: str
) -> int:
    key = (instrument_key, field_name)
    sequence = counters.get(key, 0)
    counters[key] = sequence + 1
    return sequence


class ReplaySource:

    def __init__(self, events: Sequence[RawMarketEvent]) -> None:
        self._events = sorted(events, key=lambda event: (event.canonical_ts, event.event_id))
        self._tick_cb: Callable[[BrokerTick], None] | None = None

    def subscribe(self, instrument_keys: Sequence[str]) -> None:
        return None

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None:
        self._tick_cb = callback

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None:
        return None

    def unsubscribe_all(self) -> None:
        return None

    def pump(self) -> None:
        if self._tick_cb is None:
            return
        counters: dict[tuple[str, str], int] = {}
        for event in self._events:
            if not is_observation(event.field_name):
                continue
            self._tick_cb(
                BrokerTick(
                    instrument_key=event.instrument_key,
                    field_name=event.field_name,
                    value=event.value,
                    underlying=event.underlying,
                    sequence=next_sequence(counters, event.instrument_key, event.field_name),
                    exchange_ts=event.exchange_ts,
                )
            )
