"""Replay a stored trading day — both as a read and as a push source into the collector.

The append-only raw layer is enough to reproduce a day's event stream. Two surfaces:

* :func:`replay_day` — the read-only half: return a stored day's events in canonical order,
  touching only the store. The actor's replay path consumes this directly.
* :class:`ReplaySource` — the push half: a :class:`~collectors.MarketDataAdapter` that
  re-emits stored events as the *same* unified :class:`~collectors.BrokerTick` into the
  *same* :class:`~collectors.RawCollector` the live feed drives. This is the seam behind the
  idempotent-recapture guarantee (ADR 0027 §4): live and replay differ only in the source of
  events, never in the collection code. Because the live adapter and the replay source assign
  ``sequence`` by the same deterministic rule — a per-(instrument, field) ordinal in canonical
  order — a replayed observation hashes to the same ``event_id`` as the live one, so
  re-collecting a captured day into the same store writes nothing new: the raw partition is
  unchanged, exactly-once.
"""

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
    """Return a stored day's events in canonical order, touching only the store.

    Ordered by ``(canonical_ts, event_id)`` so the replayed stream is deterministic
    regardless of the order partitions were written. Optionally scoped to one
    underlying. No broker connection is made.
    """
    if underlying is not None:
        events = store.read(_RAW_MARKET_EVENTS, trade_date=trade_date, underlying=underlying)
    else:
        events = store.read(_RAW_MARKET_EVENTS, trade_date=trade_date)
    return sorted(events, key=lambda event: (event.canonical_ts, event.event_id))


def next_sequence(
    counters: dict[tuple[str, str], int], instrument_key: str, field_name: str
) -> int:
    """Deterministic per-(instrument, field) ordinal, advancing ``counters`` in place.

    The single sequence-assignment rule shared by the live emit boundary and the replay
    source: the n-th observation of one field of one instrument, in arrival/canonical order,
    gets sequence n. Driving both paths through this one function is what makes a replayed
    observation hash to the same content-addressed ``event_id`` as the live one.
    """
    key = (instrument_key, field_name)
    sequence = counters.get(key, 0)
    counters[key] = sequence + 1
    return sequence


class ReplaySource:
    """A push :class:`~collectors.MarketDataAdapter` that re-emits stored events as ticks.

    Construct it with the stored events for the day (typically :func:`replay_day`'s output).
    Wired into a :class:`~collectors.RawCollector` like any adapter; calling :meth:`pump`
    pushes each stored event back through the collector's tick callback as a unified
    :class:`BrokerTick`, with ``sequence`` re-derived by the shared per-(instrument, field)
    rule so the replayed event id matches the original. No broker is involved.
    """

    def __init__(self, events: Sequence[RawMarketEvent]) -> None:
        # Canonical order so the per-(instrument, field) sequence matches the capture order.
        self._events = sorted(events, key=lambda event: (event.canonical_ts, event.event_id))
        self._tick_cb: Callable[[BrokerTick], None] | None = None

    def subscribe(self, instrument_keys: Sequence[str]) -> None:
        """No-op: the replay source already holds every event it will emit."""
        return None

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None:
        self._tick_cb = callback

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None:
        """No-op: a replay of stored events has no live feed and therefore no faults."""
        return None

    def unsubscribe_all(self) -> None:
        return None

    def pump(self) -> None:
        """Push every stored observation through the tick callback, in canonical order.

        Each event becomes a :class:`BrokerTick` carrying its stored instrument/field/value and
        a ``sequence`` re-derived by :func:`next_sequence`, so the collector recomputes the
        same content-addressed ``event_id`` it had on live capture (the id depends only on
        instrument/field/sequence, never on the timestamps). Gap meta-events are not
        observations and are skipped — the collector records gaps from reconnects, not from the
        replayed stream.
        """
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
