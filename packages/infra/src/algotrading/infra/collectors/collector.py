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

    kind: str
    code: int | None
    message: str
    instrument_key: str | None = None


class MarketDataAdapter(Protocol):

    def subscribe(self, instrument_keys: Sequence[str]) -> None: ...

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None: ...

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None: ...

    def unsubscribe_all(self) -> None: ...


class _Clock(Protocol):

    def now(self) -> datetime: ...


class RawCollector:

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
        try:
            event = normalize_event(
                tick,
                session_id=self._session_id,
                trade_date=self._trade_date,
                receipt_ts=self._clock.now(),
            )
        except ReservedFieldError:
            _log.warning(
                "tick_with_reserved_field",
                extra={"field_name": tick.field_name, "session_id": self._session_id},
            )
            return
        if event is None:
            return
        self._enqueue(event)

    def record_reconnect(self, gap: GapInterval) -> None:
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

    def _enqueue(self, event: RawMarketEvent) -> None:
        with self._lock:
            if event.event_id in self._seen or event.event_id in self._buffered_ids:
                return
            self._buffer.append(event)
            self._buffered_ids.add(event.event_id)
            full = len(self._buffer) >= self._flush_batch_size
        if full:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            pending, self._buffer = self._buffer, []
            pending_ids, self._buffered_ids = self._buffered_ids, set()
        self._store.write(_RAW_MARKET_EVENTS, pending)
        with self._lock:
            self._seen.update(pending_ids)

    def close(self) -> CollectorSummary:
        self.flush()
        self._adapter.unsubscribe_all()
        return self.build_summary()

    def build_summary(self) -> CollectorSummary:
        events = [
            event
            for event in self._store.read(_RAW_MARKET_EVENTS)
            if event.session_id == self._session_id
        ]
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
    parts = instrument_key.split(":")
    return parts[1] if len(parts) > 1 else instrument_key
