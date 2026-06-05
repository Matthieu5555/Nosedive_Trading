"""Replay a stored trading day from disk, without reaching the broker.

The append-only raw layer is enough to reproduce a day's event stream: read the stored
events for the date and return them in canonical order. This is the read-only half of
"at least one day can be replayed from disk"; the matching seam that lets E run the
*same collector code* over replayed ticks is :class:`connectivity.ReplayBrokerSession`.
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.contracts import RawMarketEvent
from algotrading.infra.storage import ParquetStore

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
        events = [
            event
            for event in store.read(_RAW_MARKET_EVENTS)
            if event.trade_date == trade_date
        ]
    return sorted(events, key=lambda event: (event.canonical_ts, event.event_id))
