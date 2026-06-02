"""The market-data collector: subscribe, normalize, stamp, persist — append-only.

This owns the immutable, append-only, loss-aware raw layer. The tick path does only
three things — normalize the broker tick into a :class:`RawMarketEvent`, stamp the
three timestamps, and persist it — and never any analytics, per the spec's gotcha:
heavy work on the callback is the fastest way to drop market data.

Persistence is idempotent on the deterministic event id, which is what makes two
guarantees hold. A tick re-delivered after a reconnect is written exactly once,
because its id is already known. And a kill-and-restart does not corrupt the store:
on start the collector reloads the event ids already durably written for its session,
so re-feeding the same ticks writes only what is genuinely new. Events are buffered and
flushed in atomic batches through A's all-or-nothing write, so a crash mid-flush loses
the whole in-flight batch (never a partial record) and that batch is simply re-fed and
re-written on restart.

Outages are recorded as explicit gap events (loss-aware) the moment the first tick after
a reconnect reports the gap, and flushed before that tick is enqueued — so the hole is
durable no later than any observation after it, even across a crash. Pacing and
entitlement notices are classified, logged as structured events, and counted in the
daily summary.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime

from connectivity import BrokerTick, Clock, GapInterval, SessionSupervisor
from contracts import RawMarketEvent
from storage import ParquetStore
from universe import UniverseService, UnknownContractError

from .errors import ReservedFieldError
from .normalization import build_gap_event, normalize_tick
from .notices import FeedNotice, classify_feed_notice
from .summary import CollectorSummary, summarize_session

_LOG = logging.getLogger(__name__)
_RAW_MARKET_EVENTS = "raw_market_events"
_DEFAULT_FLUSH_EVERY = 256


class MarketDataCollector:
    """Collect a session's market data into the append-only raw layer.

    Construct it with the store, the universe (to resolve a tick's broker contract id
    to a canonical instrument), a session id that is *stable across restarts*, the
    session's trade date, and an injected clock for receipt timestamps. ``flush_every``
    is the atomic write-batch size.
    """

    def __init__(
        self,
        *,
        store: ParquetStore,
        universe: UniverseService,
        session_id: str,
        trade_date: date,
        clock: Clock,
        flush_every: int = _DEFAULT_FLUSH_EVERY,
    ) -> None:
        if flush_every < 1:
            raise ValueError(f"flush_every must be >= 1, got {flush_every}")
        self._store = store
        self._universe = universe
        self._session_id = session_id
        self._trade_date = trade_date
        self._clock = clock
        self._flush_every = flush_every
        self._seen: set[str] = set()
        self._buffer: list[RawMarketEvent] = []
        self._buffered_ids: set[str] = set()
        self._notices: list[FeedNotice] = []
        self._loaded = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def notices(self) -> tuple[FeedNotice, ...]:
        return tuple(self._notices)

    def collect(
        self, supervisor: SessionSupervisor, *, subscribe: Sequence[str]
    ) -> CollectorSummary:
        """Run a collection session: subscribe, persist every tick, record gaps.

        Reloads the event ids already written for this session (so a restart does not
        duplicate), subscribes each instrument, then drains the resilient stream. An
        outage is recorded the instant the first post-reconnect tick reports it, and
        flushed before that tick is even enqueued — so the gap is durable no later than
        any observation after it, and a crash can never leave a post-gap observation on
        disk with no record of the hole. A final sweep records any trailing outage that
        had no following tick (it dedupes against the inline ones by gap id). Returns the
        daily summary.
        """
        self._reload_seen_event_ids()
        for broker_contract_id in subscribe:
            supervisor.subscribe(broker_contract_id)
        for supervised in supervisor.stream():
            if supervised.gap_before is not None:
                self._record_gap(supervised.gap_before, subscribe)
                self._flush()  # durable before the post-gap observation is enqueued
            self._handle_tick(supervised.tick)
        self._flush()
        self._record_gaps(supervisor.reconnects, subscribe)
        self._flush()
        return self._build_summary(supervisor.reconnect_count, subscribe)

    def record_feed_notice(self, code: int, message: str, *, ts: datetime | None = None) -> None:
        """Classify and log a broker feed notice (pacing/entitlement) as a structured event."""
        when = ts if ts is not None else self._clock.now()
        notice = classify_feed_notice(code, message, when)
        self._notices.append(notice)
        _LOG.warning(
            "feed_notice",
            extra={
                "feed_code": code,
                "feed_kind": notice.kind,
                "session_id": self._session_id,
                "feed_message": message,
            },
        )

    def _reload_seen_event_ids(self) -> None:
        if self._loaded:
            return
        for event in self._store.read(_RAW_MARKET_EVENTS):
            if event.session_id == self._session_id:
                self._seen.add(event.event_id)
        self._loaded = True

    def _handle_tick(self, tick: BrokerTick) -> None:
        try:
            instrument = self._universe.resolve_contract(tick.broker_contract_id)
        except UnknownContractError:
            # A tick for an instrument not in the universe is a feed anomaly: it is
            # surfaced as a structured log, not silently dropped and not fatal to the
            # whole session, so collection keeps running unsupervised.
            _LOG.warning(
                "tick_for_unknown_contract",
                extra={
                    "broker_contract_id": tick.broker_contract_id,
                    "session_id": self._session_id,
                },
            )
            return
        try:
            event = normalize_tick(
                tick,
                instrument_key=instrument.canonical(),
                underlying=instrument.underlying_symbol,
                session_id=self._session_id,
                trade_date=self._trade_date,
                receipt_ts=self._clock.now(),
            )
        except ReservedFieldError:
            # A tick whose field name collides with the reserved meta namespace is a
            # misconfigured feed: logged and skipped, never stored as a fake meta-event.
            _LOG.warning(
                "tick_with_reserved_field",
                extra={"field_name": tick.field_name, "session_id": self._session_id},
            )
            return
        self._enqueue(event)

    def _record_gaps(self, outages: Sequence[GapInterval], subscribe: Sequence[str]) -> None:
        for gap in outages:
            self._record_gap(gap, subscribe)

    def _record_gap(self, gap: GapInterval, subscribe: Sequence[str]) -> None:
        """Enqueue one gap event per subscribed instrument for a single outage.

        Idempotent: a gap is content-addressed on its resumption time, so recording the
        same outage inline (on the first post-reconnect tick) and again in the trailing
        sweep writes it exactly once.
        """
        for broker_contract_id in subscribe:
            try:
                instrument = self._universe.resolve_contract(broker_contract_id)
            except UnknownContractError:
                # A subscribed id not in the universe means no gap can be attributed to
                # it; surfaced as a structured log rather than dropped without a trace.
                _LOG.warning(
                    "gap_for_unknown_contract",
                    extra={
                        "broker_contract_id": broker_contract_id,
                        "session_id": self._session_id,
                    },
                )
                continue
            self._enqueue(
                build_gap_event(
                    instrument_key=instrument.canonical(),
                    underlying=instrument.underlying_symbol,
                    session_id=self._session_id,
                    trade_date=self._trade_date,
                    gap=gap,
                )
            )

    def _enqueue(self, event: RawMarketEvent) -> None:
        if event.event_id in self._seen or event.event_id in self._buffered_ids:
            return  # idempotent: a re-delivered tick or an already-buffered duplicate
        self._buffer.append(event)
        self._buffered_ids.add(event.event_id)
        if len(self._buffer) >= self._flush_every:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        # One atomic write of the whole batch. Mark events seen only after it commits,
        # so a failed flush leaves them unseen — to be re-fed and retried on restart.
        self._store.write(_RAW_MARKET_EVENTS, self._buffer)
        self._seen.update(self._buffered_ids)
        self._buffer = []
        self._buffered_ids = set()

    def _build_summary(
        self, reconnect_count: int, subscribe: Sequence[str]
    ) -> CollectorSummary:
        events = [
            event
            for event in self._store.read(_RAW_MARKET_EVENTS)
            if event.session_id == self._session_id
        ]
        subscribed_keys: set[str] = set()
        for broker_contract_id in subscribe:
            try:
                subscribed_keys.add(self._universe.resolve_contract(broker_contract_id).canonical())
            except UnknownContractError:
                continue
        return summarize_session(
            events,
            session_id=self._session_id,
            trade_date=self._trade_date,
            subscribed_keys=subscribed_keys,
            reconnect_count=reconnect_count,
            notices=tuple(self._notices),
        )
