"""The raw collector: capture a broker's market-data stream into the immutable raw layer.

The collector owns no broker code. It consumes a broker-agnostic adapter that delivers ticks
and feed faults, normalizes each tick into a ``RawMarketEvent``, and flushes events to the store
in batches (one Parquet file per batch — a file per tick would be pathological). A feed fault
(pacing or entitlement) is logged as a structured event and counted rather than swallowed, so a
degraded feed is visible in the session summary instead of silently thinning the data.

Reconnect and heartbeat live in the broker session, not here; the collector is only the capture
path, so the same code records a live stream and (via the replay source) a stored one.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from algotrading.core.log import get_logger

from .normalize import BrokerTick, normalize_event

_log = get_logger(__name__)


@dataclass(frozen=True)
class FeedFault:
    """A broker-agnostic market-data fault. ``kind`` partitions faults for the summary
    (``"pacing"``, ``"entitlement"``, or ``"other"``); ``code``/``message`` keep the vendor
    detail for operators; ``instrument_key`` is set when the fault is contract-specific."""

    kind: str
    code: int | None
    message: str
    instrument_key: str | None = None


class MarketDataAdapter(Protocol):
    """Broker-agnostic market-data surface. The adapter turns vendor callbacks into ticks and
    faults; the rest of the stack never sees a broker SDK type."""

    def subscribe(self, instrument_keys: Sequence[str]) -> None: ...

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None: ...

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None: ...

    def unsubscribe_all(self) -> None: ...


class EventWriter(Protocol):
    """Append-only sink for raw events (the raw store satisfies this)."""

    def write_events(self, events: Sequence[object]) -> None: ...


class RawCollector:
    """Capture an adapter's market-data stream into the raw store, batching writes."""

    def __init__(
        self,
        *,
        adapter: MarketDataAdapter,
        writer: EventWriter,
        clock: Callable[[], datetime],
        session_id: str,
        flush_batch_size: int = 500,
    ) -> None:
        self._adapter = adapter
        self._writer = writer
        self._clock = clock
        self._session_id = session_id
        self._flush_batch_size = flush_batch_size
        self._buffer: list[object] = []
        self._seq = 0
        self.events_collected = 0
        self.faults: Counter[str] = Counter()
        # ingest() is called from the streaming adapter's listener thread AND, for out-of-band
        # pollers, from the caller's thread — so the buffer/counter mutations need a lock.
        self._lock = threading.Lock()
        adapter.set_tick_callback(self._on_tick)
        adapter.set_fault_callback(self._on_fault)

    def start(self, instrument_keys: Sequence[str]) -> None:
        """Subscribe to the configured instruments; ticks then flow into the store."""
        self._adapter.subscribe(instrument_keys)

    def _on_tick(self, tick: BrokerTick) -> None:
        self.ingest(tick)

    def ingest(self, tick: BrokerTick) -> None:
        """Normalize, count and buffer one tick into the raw layer.

        The streaming adapter callback and any out-of-band poller (e.g. a periodic underlying-spot
        probe) share this single path, so every captured observation is normalized and batched
        identically regardless of how it arrived. Thread-safe: the buffer/counter mutations are
        guarded so a listener thread and a poller thread cannot corrupt sequencing or drop events.
        """
        with self._lock:
            self._seq += 1
            self.events_collected += 1
            event = normalize_event(
                tick,
                collector_session_id=self._session_id,
                event_id=f"evt-{self._seq}",
                receipt_ts=self._clock(),
            )
            self._buffer.append(event)
            full = len(self._buffer) >= self._flush_batch_size
        if full:
            self.flush()

    def _on_fault(self, fault: FeedFault) -> None:
        self.faults[fault.kind] += 1
        _log.warning(
            "market-data feed fault: %s",
            fault.kind,
            extra={
                "fault_kind": fault.kind,
                "code": fault.code,
                "fault_message": fault.message,
                "instrument_key": fault.instrument_key,
                "session_id": self._session_id,
            },
        )

    def flush(self) -> None:
        """Persist and clear the buffer; a no-op when there is nothing pending.

        The buffer is swapped out under the lock, then written outside it: the swap is atomic
        against concurrent ``ingest`` calls, while the (slower) store write never holds the lock.
        """
        with self._lock:
            if not self._buffer:
                return
            pending, self._buffer = self._buffer, []
        self._writer.write_events(pending)

    def close(self) -> None:
        """Flush any pending events and drop all subscriptions."""
        self.flush()
        self._adapter.unsubscribe_all()
