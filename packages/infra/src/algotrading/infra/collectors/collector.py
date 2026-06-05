"""The raw collector: capture a broker's market-data stream into the immutable raw layer.

The collector owns no broker code. It consumes a broker-agnostic push
:class:`MarketDataAdapter` that delivers ticks and feed faults, normalizes each tick into
A's :class:`~algotrading.infra.contracts.RawMarketEvent`, and flushes events to the store
in atomic batches (one Parquet file per batch — a file per tick would be pathological).
Reconnect and heartbeat live in the broker session beneath the adapter, never here; the
collector is only the capture path, so the same code records a live stream and (via the
replay source) a stored one.

Persistence is idempotent on the deterministic event id, which is what makes two
guarantees hold. A tick re-delivered after a reconnect is written exactly once, because its
id (content-addressed on ``instrument_key``/``field_name``/``sequence``) is already known.
And a kill-and-restart does not corrupt the store: on construction the collector reloads the
event ids already durably written for its session, so re-feeding the same ticks writes only
what is genuinely new. Events are buffered and flushed in atomic batches through A's
all-or-nothing write, so a crash mid-flush loses the whole in-flight batch (never a partial
record) and that batch is simply re-fed and re-written on restart.

Outages are recorded as explicit gap events (loss-aware): the session beneath the adapter
owns reconnect and surfaces each :class:`~algotrading.infra.connectivity.GapInterval`, which
the collector turns into one content-addressed gap meta-event per subscribed instrument.
Pacing and entitlement faults are classified, logged as structured events, and counted into
the session summary rather than swallowed.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from algotrading.core.log import get_logger
from algotrading.infra.connectivity import GapInterval
from algotrading.infra.contracts import RawMarketEvent
from algotrading.infra.storage import ParquetStore

from .errors import ReservedFieldError
from .normalization import build_gap_event
from .normalize import BrokerTick, normalize_event
from .notices import FeedNotice
from .summary import CollectorSummary, summarize_session

_log = get_logger(__name__)

_RAW_MARKET_EVENTS = "raw_market_events"
_DEFAULT_FLUSH_BATCH_SIZE = 256


@dataclass(frozen=True, slots=True)
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


class _Clock(Protocol):
    """The minimal clock the collector needs: a UTC ``now()`` for receipt stamps."""

    def now(self) -> datetime: ...


class RawCollector:
    """Capture a push adapter's market-data stream into the append-only raw layer.

    Construct it with the store, the push adapter, a session id that is *stable across
    restarts* (so a restart resumes the same session, not a fresh one), the session's trade
    date, an injected clock for receipt timestamps, and the canonical instrument keys the
    session subscribes (used to attribute gap meta-events). ``flush_batch_size`` is the
    atomic write-batch size.
    """

    def __init__(
        self,
        *,
        store: ParquetStore,
        adapter: MarketDataAdapter,
        session_id: str,
        trade_date: date,
        clock: _Clock,
        subscribed_keys: Sequence[str] = (),
        flush_batch_size: int = _DEFAULT_FLUSH_BATCH_SIZE,
    ) -> None:
        if flush_batch_size < 1:
            raise ValueError(f"flush_batch_size must be >= 1, got {flush_batch_size}")
        self._store = store
        self._adapter = adapter
        self._session_id = session_id
        self._trade_date = trade_date
        self._clock = clock
        self._subscribed_keys = tuple(subscribed_keys)
        self._flush_batch_size = flush_batch_size
        self._seen: set[str] = set()
        self._buffer: list[RawMarketEvent] = []
        self._buffered_ids: set[str] = set()
        self.faults: Counter[str] = Counter()
        self._reconnect_count = 0
        self._loaded = False
        # _on_tick is called from the streaming adapter's listener thread AND, for out-of-band
        # pollers and the replay source, from the caller's thread — so the buffer/counter
        # mutations need a lock.
        self._lock = threading.Lock()
        self._reload_seen_event_ids()
        adapter.set_tick_callback(self._on_tick)
        adapter.set_fault_callback(self._on_fault)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    def start(self, instrument_keys: Sequence[str]) -> None:
        """Subscribe to the given instruments; ticks then flow into the store via the callback.

        The subscribed set is remembered (for gap attribution) and the adapter is told to
        subscribe. A re-start over the same store and session re-feeds without duplicating,
        because the event ids already written were reloaded at construction.
        """
        if instrument_keys:
            self._subscribed_keys = tuple(instrument_keys)
        self._adapter.subscribe(instrument_keys)

    def _reload_seen_event_ids(self) -> None:
        if self._loaded:
            return
        for event in self._store.read(_RAW_MARKET_EVENTS):
            if event.session_id == self._session_id:
                self._seen.add(event.event_id)
        self._loaded = True

    def _on_tick(self, tick: BrokerTick) -> None:
        """Normalize and buffer one observation; absent/reserved ticks are skipped, not stored."""
        try:
            event = normalize_event(
                tick,
                session_id=self._session_id,
                trade_date=self._trade_date,
                receipt_ts=self._clock.now(),
            )
        except ReservedFieldError:
            # A tick whose field name collides with the reserved meta namespace is a
            # misconfigured feed: logged and skipped, never stored as a fake meta-event.
            _log.warning(
                "tick_with_reserved_field",
                extra={"field_name": tick.field_name, "session_id": self._session_id},
            )
            return
        if event is None:
            # An absent (None / non-finite / categorical) value is not a storable observation;
            # its absence shows up as reduced coverage in the summary, not as a fake record.
            return
        self._enqueue(event)

    def record_reconnect(self, gap: GapInterval) -> None:
        """Record one outage: a content-addressed gap meta-event per subscribed instrument.

        The session beneath the adapter owns reconnect and reports each :class:`GapInterval`;
        the collector turns it into the loss-aware record. Idempotent — a gap is
        content-addressed on its resumption time, so the same outage recorded twice (a restart
        that reproduces it) is written exactly once. Flushed immediately, so the hole is
        durable no later than any observation after it.
        """
        self._reconnect_count += 1
        for instrument_key in self._subscribed_keys:
            self._enqueue(
                build_gap_event(
                    instrument_key=instrument_key,
                    underlying=_underlying_of(instrument_key),
                    session_id=self._session_id,
                    trade_date=self._trade_date,
                    gap=gap,
                )
            )
        self.flush()  # durable before any post-gap observation is enqueued

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

    def _enqueue(self, event: RawMarketEvent) -> None:
        with self._lock:
            if event.event_id in self._seen or event.event_id in self._buffered_ids:
                return  # idempotent: a re-delivered tick or an already-buffered duplicate
            self._buffer.append(event)
            self._buffered_ids.add(event.event_id)
            full = len(self._buffer) >= self._flush_batch_size
        if full:
            self.flush()

    def flush(self) -> None:
        """Persist and clear the buffer; a no-op when there is nothing pending.

        The buffer is swapped out under the lock, then written outside it: the swap is atomic
        against concurrent ``_on_tick`` calls, while the (slower) store write never holds the
        lock. Event ids are marked seen only after the write commits, so a failed flush leaves
        them unseen — to be re-fed and retried on restart, never silently lost.
        """
        with self._lock:
            if not self._buffer:
                return
            pending, self._buffer = self._buffer, []
            pending_ids, self._buffered_ids = self._buffered_ids, set()
        self._store.write(_RAW_MARKET_EVENTS, pending)
        with self._lock:
            self._seen.update(pending_ids)

    def close(self) -> CollectorSummary:
        """Flush pending events, drop subscriptions, and return the session's daily summary."""
        self.flush()
        self._adapter.unsubscribe_all()
        return self.build_summary()

    def build_summary(self) -> CollectorSummary:
        """Summarize the session from the events persisted under its id (pure over the store).

        Pacing/entitlement counts come from the faults this session observed; the same
        :func:`summarize_session` builder serves both the push fault and the pull notice, so
        there is one summary code path. ``OTHER`` faults are logged and counted in ``faults``
        but are neither pacing nor entitlement, so they do not move those two summary fields.
        """
        events = [
            event
            for event in self._store.read(_RAW_MARKET_EVENTS)
            if event.session_id == self._session_id
        ]
        # The summary counts notices by kind; a push fault already carries its kind, so one
        # FeedNotice per counted fault feeds the one summary builder (no second code path). The
        # synthetic code/message/ts are unused by the summary, which keys only on kind.
        now = self._clock.now()
        notices = tuple(
            FeedNotice(kind=kind, code=0, message="", ts=now)
            for kind, count in self.faults.items()
            for _ in range(count)
        )
        return summarize_session(
            events,
            session_id=self._session_id,
            trade_date=self._trade_date,
            subscribed_keys=set(self._subscribed_keys),
            reconnect_count=self._reconnect_count,
            notices=notices,
        )


def _underlying_of(instrument_key: str) -> str:
    """Recover the underlying symbol from a canonical instrument key.

    The canonical key embeds the symbol as its second colon-separated segment
    (``OPT:BTC:...`` / ``UND:BTC:...``); a key without that shape falls back to the whole
    key, which keeps a gap event attributable rather than dropping it.
    """
    parts = instrument_key.split(":")
    return parts[1] if len(parts) > 1 else instrument_key
