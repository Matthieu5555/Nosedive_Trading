from __future__ import annotations

from collections.abc import Callable, Sequence

from .collector import FeedFault, MarketDataAdapter
from .normalize import BrokerTick, is_storable_observation
from .replay import next_sequence


class SequenceStamping:

    def __init__(self, adapter: MarketDataAdapter) -> None:
        self._adapter = adapter
        self._counters: dict[tuple[str, str], int] = {}
        self._downstream: Callable[[BrokerTick], None] | None = None
        adapter.set_tick_callback(self._stamp)

    def subscribe(self, instrument_keys: Sequence[str]) -> None:
        self._adapter.subscribe(instrument_keys)

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None:
        self._downstream = callback

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None:
        self._adapter.set_fault_callback(callback)

    def unsubscribe_all(self) -> None:
        self._adapter.unsubscribe_all()

    def _stamp(self, tick: BrokerTick) -> None:
        if self._downstream is None:
            return
        if not is_storable_observation(tick):
            self._downstream(tick)
            return
        sequence = next_sequence(self._counters, tick.instrument_key, tick.field_name)
        self._downstream(
            BrokerTick(
                instrument_key=tick.instrument_key,
                field_name=tick.field_name,
                value=tick.value,
                underlying=tick.underlying,
                sequence=sequence,
                provider=tick.provider,
                exchange_ts=tick.exchange_ts,
                contract_id_broker=tick.contract_id_broker,
            )
        )
