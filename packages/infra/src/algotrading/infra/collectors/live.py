"""Live capture: stamp a feed's ticks with a stable sequence and drive the collector.

A live push adapter emits a :class:`~collectors.BrokerTick` per observation but does not know
the feed's stable ordinal, so it leaves ``sequence`` at its default. :class:`SequenceStamping`
wraps such an adapter and assigns each tick the per-(instrument, field) ordinal — the *same*
rule the replay source uses (:func:`collectors.next_sequence`) — so a captured day and its
replay produce identical content-addressed ids, and a kill/restart that re-derives the stream
in canonical order writes each event exactly once.

The wrapper sits between the broker adapter and the :class:`~collectors.RawCollector`: the
collector still sees a plain :class:`~collectors.MarketDataAdapter`, and reconnect/backoff stay
in the session beneath the adapter (``connectivity.SessionSupervisor``), which surfaces each
outage to the collector via :meth:`RawCollector.record_reconnect`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .collector import FeedFault, MarketDataAdapter
from .normalize import BrokerTick
from .replay import next_sequence


class SequenceStamping:
    """Wrap a push adapter so every emitted tick carries a stable per-(instrument, field) ordinal.

    Construct it around the broker adapter; pass *this* to the collector. It forwards
    ``subscribe``/``unsubscribe_all`` and the fault callback untouched, and intercepts the tick
    callback to stamp ``sequence`` before the collector sees the tick — so the adapter stays
    sequence-agnostic and one rule assigns the ordinal for every broker leaf.
    """

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
