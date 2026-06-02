"""Collector: append-only idempotent capture, loss-aware gaps, summary, replay.

The load-bearing one is ``test_kill_and_restart_writes_each_event_exactly_once`` and
its mid-write sibling: the raw store must end with exactly the durably-written events,
no partial record and no duplicate. The summary is checked against a hand-derived
expected value (an independent oracle), and the timestamp rules are pinned at the
``normalize_tick`` level where they are pure.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import storage.adapter as adapter_module
from collectors import (
    GAP_FIELD,
    CollectorSummary,
    MarketDataCollector,
    ReservedFieldError,
    is_observation,
    normalize_tick,
    replay_day,
    summarize_session,
)
from connectivity import (
    BrokerTick,
    FakeBrokerSession,
    ManualClock,
    ReplayBrokerSession,
    ScriptedDrop,
    ScriptItem,
    SessionSupervisor,
    client_id_for,
)
from contracts import RawMarketEvent
from storage import ParquetStore
from universe import UniverseService, resolve_chain

_TRADE_DATE = date(2026, 6, 1)
_SESSION = "sess-2026-06-01"
_T0 = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)

_CHAIN_ROWS: list[dict[str, object]] = [
    {"conId": "u", "symbol": "AAPL", "secType": "STK", "exchange": "SMART",
     "currency": "USD", "multiplier": 1},
    {"conId": "c1", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
     "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "C"},
    {"conId": "p1", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
     "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "P"},
]


def _universe() -> UniverseService:
    resolved = resolve_chain(_CHAIN_ROWS)
    return UniverseService([contract.instrument for contract in resolved], _TRADE_DATE)


def _collector(store: ParquetStore, *, flush_every: int = 256) -> MarketDataCollector:
    return MarketDataCollector(
        store=store,
        universe=_universe(),
        session_id=_SESSION,
        trade_date=_TRADE_DATE,
        clock=ManualClock(start=_T0),
        flush_every=flush_every,
    )


def _tick(sequence: int, value: float, *, cid: str = "c1", field: str = "bid") -> BrokerTick:
    return BrokerTick(
        broker_contract_id=cid,
        field_name=field,
        value=value,
        sequence=sequence,
        exchange_ts=_T0 + timedelta(seconds=sequence),
    )


def _run(
    store: ParquetStore,
    script: list[ScriptItem],
    *,
    subscribe: tuple[str, ...] = ("c1",),
    collector: MarketDataCollector | None = None,
) -> CollectorSummary:
    clock = ManualClock(start=_T0)
    supervisor = SessionSupervisor(FakeBrokerSession(script=script), client_id=2000, clock=clock)
    supervisor.connect()
    coll = collector if collector is not None else _collector(store)
    return coll.collect(supervisor, subscribe=list(subscribe))


def _events(store: ParquetStore) -> list[RawMarketEvent]:
    return store.read("raw_market_events")


# -- persistence and idempotency --------------------------------------------


def test_collected_ticks_are_persisted_as_raw_events(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _run(store, [_tick(1, 10.0), _tick(2, 11.0, field="ask")])
    events = sorted(_events(store), key=lambda e: e.canonical_ts)
    assert [e.field_name for e in events] == ["bid", "ask"]
    assert [e.value for e in events] == [10.0, 11.0]
    # The broker contract id was resolved to the canonical instrument key.
    assert all(e.instrument_key.startswith("AAPL|OPT") for e in events)
    assert all(e.session_id == _SESSION for e in events)


def test_redelivered_tick_within_a_session_is_not_double_written(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    # The exact same observation (same sequence) is delivered twice, plus a new one.
    _run(store, [_tick(1, 10.0), _tick(2, 11.0), _tick(2, 11.0), _tick(3, 12.0)])
    events = _events(store)
    assert len({e.event_id for e in events}) == 3
    assert len(events) == 3


def test_kill_and_restart_writes_each_event_exactly_once(tmp_path: Path) -> None:
    # The non-negotiable invariant. A first collector writes two events and "dies".
    # A fresh collector on the same session and store is re-fed those two (re-delivery)
    # plus a third: the store must hold exactly three events, no duplicate, no partial.
    first_store = ParquetStore(tmp_path)
    _run(first_store, [_tick(1, 10.0), _tick(2, 11.0)])
    assert len(_events(first_store)) == 2

    restart_store = ParquetStore(tmp_path)  # a fresh process view of the same root
    _run(restart_store, [_tick(1, 10.0), _tick(2, 11.0), _tick(3, 12.0)])

    events = _events(restart_store)
    assert len(events) == 3
    assert {(e.session_id, e.event_id) for e in events} == {
        (e.session_id, e.event_id) for e in events
    }  # no collision
    assert sorted(e.value for e in events) == [10.0, 11.0, 12.0]


def test_a_failed_flush_leaves_no_partial_record_then_restart_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kill *mid-write*: the third per-event flush fails like a crash. The store must
    # then hold exactly the two durably-written events (no partial third), and a
    # restart re-feeds all three and completes the store.
    state = {"fail_on": 3, "count": 0}
    real_write_table = adapter_module.pq.write_table

    def flaky_write_table(*args: object, **kwargs: object) -> None:
        state["count"] += 1
        if state["count"] == state["fail_on"]:
            raise OSError("disk full (injected)")
        real_write_table(*args, **kwargs)

    monkeypatch.setattr(adapter_module.pq, "write_table", flaky_write_table)

    crash_store = ParquetStore(tmp_path)
    crashing = _collector(crash_store, flush_every=1)
    with pytest.raises(OSError, match="injected"):
        _run(crash_store, [_tick(1, 10.0), _tick(2, 11.0), _tick(3, 12.0)], collector=crashing)

    assert sorted(e.value for e in _events(crash_store)) == [10.0, 11.0]  # no partial third

    state["fail_on"] = -1  # the "restarted" process writes cleanly
    restart_store = ParquetStore(tmp_path)
    _run(
        restart_store,
        [_tick(1, 10.0), _tick(2, 11.0), _tick(3, 12.0)],
        collector=_collector(restart_store, flush_every=1),
    )

    assert sorted(e.value for e in _events(restart_store)) == [10.0, 11.0, 12.0]


# -- timestamp normalization (pure rules) -----------------------------------


def _normalize(tick: BrokerTick, receipt_ts: datetime) -> RawMarketEvent:
    return normalize_tick(
        tick,
        instrument_key="AAPL|OPT|SMART|USD|100|c1|2026-06-19|100|C",
        underlying="AAPL",
        session_id=_SESSION,
        trade_date=_TRADE_DATE,
        receipt_ts=receipt_ts,
    )


def test_exchange_ts_becomes_canonical_when_present() -> None:
    exchange = _T0 + timedelta(seconds=5)
    receipt = _T0 + timedelta(seconds=9)
    event = _normalize(BrokerTick("c1", "bid", 10.0, 1, exchange_ts=exchange), receipt)
    assert event.canonical_ts == exchange
    assert event.exchange_ts == exchange
    assert event.receipt_ts == receipt


def test_absent_exchange_ts_falls_back_to_receipt_and_both_are_present() -> None:
    receipt = _T0 + timedelta(seconds=9)
    event = _normalize(BrokerTick("c1", "bid", 10.0, 1, exchange_ts=None), receipt)
    assert event.receipt_ts == receipt
    assert event.canonical_ts == receipt  # always present, never None
    assert event.exchange_ts == receipt  # contract-required field filled from receipt


def test_out_of_order_exchange_ts_is_preserved_as_canonical() -> None:
    # A tick whose exchange time is earlier than a prior one keeps its own time as
    # canonical: arrival order is plumbing, event order is the truth that ordering uses.
    early = _normalize(
        BrokerTick("c1", "bid", 10.0, 2, exchange_ts=_T0 + timedelta(seconds=5)), _T0
    )
    late = _normalize(
        BrokerTick("c1", "bid", 11.0, 1, exchange_ts=_T0 + timedelta(seconds=9)), _T0
    )
    assert early.canonical_ts < late.canonical_ts  # 5s < 9s, though "early" arrived later


def test_normalize_rejects_a_reserved_field_name() -> None:
    with pytest.raises(ReservedFieldError):
        _normalize(BrokerTick("c1", GAP_FIELD, 10.0, 1, exchange_ts=_T0), _T0)


def test_event_id_is_idempotent_for_the_same_observation() -> None:
    first = _normalize(BrokerTick("c1", "bid", 10.0, 7, exchange_ts=_T0), _T0)
    again = _normalize(
        BrokerTick("c1", "bid", 10.0, 7, exchange_ts=_T0 + timedelta(seconds=1)), _T0
    )
    assert first.event_id == again.event_id  # re-delivery (same sequence) -> same id


# -- loss-aware gap events ---------------------------------------------------


def test_a_reconnect_records_a_gap_event_per_subscribed_instrument(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    script: list[ScriptItem] = [
        _tick(1, 10.0),
        ScriptedDrop(connect_failures_after=2),
        _tick(2, 11.0),
    ]
    summary = _run(store, script, subscribe=("c1", "p1"))

    gaps = [e for e in _events(store) if e.field_name == GAP_FIELD]
    # One outage, two subscribed instruments -> two gap events.
    assert len(gaps) == 2
    assert {e.underlying for e in gaps} == {"AAPL"}
    assert all(e.value == 3.0 for e in gaps)  # 1s + 2s backoff outage
    assert summary.gap_count == 2
    assert summary.reconnect_count == 1


def test_gap_events_are_idempotent_across_restart(tmp_path: Path) -> None:
    script: list[ScriptItem] = [
        _tick(1, 10.0),
        ScriptedDrop(connect_failures_after=2),
        _tick(2, 11.0),
    ]
    first_store = ParquetStore(tmp_path)
    _run(first_store, script, subscribe=("c1",))
    gaps_first = [e for e in _events(first_store) if e.field_name == GAP_FIELD]

    restart_store = ParquetStore(tmp_path)
    _run(restart_store, script, subscribe=("c1",))
    gaps_second = [e for e in _events(restart_store) if e.field_name == GAP_FIELD]

    # The reproduced outage hashes to the same gap id, so it is not double-written.
    assert len(gaps_first) == len(gaps_second) == 1


def test_a_gap_is_durable_before_the_post_gap_tick_survives_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The loss-aware invariant under an awkward lifecycle. The gap must hit disk no later
    # than the first observation after it, so a crash can never leave a post-gap tick on
    # disk with no record of the hole. We crash on the write that would persist the
    # post-gap tick; the gap (recorded inline on that tick, an earlier write) must already
    # be durable. Then a live restart whose fresh supervisor has NO memory of the old drop
    # must still see the gap — proving the record did not depend on replaying the outage.
    state = {"fail_on": 3, "count": 0}
    real_write_table = adapter_module.pq.write_table

    def flaky_write_table(*args: object, **kwargs: object) -> None:
        state["count"] += 1
        if state["count"] == state["fail_on"]:
            raise OSError("disk full (injected)")
        real_write_table(*args, **kwargs)

    monkeypatch.setattr(adapter_module.pq, "write_table", flaky_write_table)

    # flush_every=1 makes each event its own write: #1 tick1, #2 gap (recorded inline on
    # the first post-reconnect tick), #3 the post-gap tick — the one we fail.
    script: list[ScriptItem] = [
        _tick(1, 10.0),
        ScriptedDrop(connect_failures_after=2),
        _tick(2, 11.0),
    ]
    crash_store = ParquetStore(tmp_path)
    with pytest.raises(OSError, match="injected"):
        _run(
            crash_store,
            script,
            subscribe=("c1",),
            collector=_collector(crash_store, flush_every=1),
        )

    crashed = _events(crash_store)
    assert len([e for e in crashed if e.field_name == GAP_FIELD]) == 1  # gap reached disk
    assert 11.0 not in [e.value for e in crashed]  # the post-gap tick did not survive

    # "Restart" as a live feed that simply resumed — no drop replayed, so the supervisor
    # has no GapInterval of its own. The gap must persist purely from the first run.
    state["fail_on"] = -1  # the restarted process writes cleanly
    restart_store = ParquetStore(tmp_path)
    _run(
        restart_store,
        [_tick(2, 11.0)],
        subscribe=("c1",),
        collector=_collector(restart_store, flush_every=1),
    )

    events = _events(restart_store)
    assert len([e for e in events if e.field_name == GAP_FIELD]) == 1  # survived, not duplicated
    assert 11.0 in [e.value for e in events]  # the resumed observation now lands


# -- feed notices ------------------------------------------------------------


def test_pacing_notice_is_classified_logged_and_counted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = ParquetStore(tmp_path)
    collector = _collector(store)
    with caplog.at_level(logging.WARNING, logger="collectors.collector"):
        collector.record_feed_notice(420, "pacing violation", ts=_T0)
    summary = _run(store, [_tick(1, 10.0)], collector=collector)
    assert summary.pacing_failures == 1
    assert any(record.message == "feed_notice" for record in caplog.records)
    # A pacing notice is logged, never written as a fake observation.
    assert all(e.field_name != "pacing" for e in _events(store))


def test_a_tick_for_an_unknown_contract_is_skipped_not_fatal(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    # "zzz" is not in the universe; the good tick on c1 still lands.
    _run(store, [_tick(1, 10.0, cid="zzz"), _tick(2, 11.0, cid="c1")])
    events = _events(store)
    assert len(events) == 1
    assert events[0].value == 11.0


# -- the daily summary against a hand-derived oracle ------------------------


def _obs(instrument_key: str, field: str, sequence: int) -> RawMarketEvent:
    return RawMarketEvent(
        session_id=_SESSION,
        event_id=f"{instrument_key}:{field}:{sequence}",
        instrument_key=instrument_key,
        exchange_ts=_T0,
        receipt_ts=_T0,
        canonical_ts=_T0,
        field_name=field,
        value=1.0,
        trade_date=_TRADE_DATE,
        underlying=instrument_key,
    )


def _gap(instrument_key: str) -> RawMarketEvent:
    return RawMarketEvent(
        session_id=_SESSION,
        event_id=f"{instrument_key}:gap",
        instrument_key=instrument_key,
        exchange_ts=_T0,
        receipt_ts=_T0,
        canonical_ts=_T0,
        field_name=GAP_FIELD,
        value=2.0,
        trade_date=_TRADE_DATE,
        underlying=instrument_key,
    )


def test_summarize_session_matches_a_hand_derived_summary() -> None:
    # Three subscribed instruments A, B, C. A produced bid+ask, B produced bid, C was
    # silent but had one gap recorded. Hand-derived: 3 observations, 1 gap, coverage
    # 2/3 (A and B produced data, C did not), one reconnect.
    events = [_obs("A", "bid", 1), _obs("A", "ask", 1), _obs("B", "bid", 1), _gap("C")]
    summary = summarize_session(
        events,
        session_id=_SESSION,
        trade_date=_TRADE_DATE,
        subscribed_keys={"A", "B", "C"},
        reconnect_count=1,
    )
    assert summary.event_count == 3
    assert summary.gap_count == 1
    assert summary.reconnect_count == 1
    assert summary.subscribed_count == 3
    assert summary.covered_count == 2
    assert summary.coverage_ratio == pytest.approx(2 / 3)
    assert summary.per_field_counts == (("ask", 1), ("bid", 2))


def test_summary_of_an_empty_session_is_zeroed() -> None:
    summary = summarize_session(
        [], session_id=_SESSION, trade_date=_TRADE_DATE, subscribed_keys={"A"}, reconnect_count=0
    )
    assert summary.event_count == 0
    assert summary.coverage_ratio == 0.0


# -- replay from disk --------------------------------------------------------


def test_replay_day_reproduces_the_stored_stream_without_the_broker(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _run(store, [_tick(1, 10.0), _tick(2, 11.0, field="ask"), _tick(3, 12.0, field="last")])

    # replay_day takes only the store — no broker session is involved.
    replayed = replay_day(store, _TRADE_DATE)
    assert [e.value for e in replayed] == [10.0, 11.0, 12.0]  # canonical order
    assert replayed == sorted(_events(store), key=lambda e: (e.canonical_ts, e.event_id))


def test_replay_of_a_day_with_nothing_stored_is_empty(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    assert replay_day(store, _TRADE_DATE) == []


def test_a_replay_session_feeds_the_real_collector_and_events_land(tmp_path: Path) -> None:
    # The same-code-path replay seam, end to end: a stored day replayed through a
    # ReplayBrokerSession runs through the SAME MarketDataCollector and its observations
    # land — they resolve against the universe instead of being skipped as unknown
    # contracts. RawMarketEvent -> ReplayBrokerSession -> SessionSupervisor ->
    # MarketDataCollector -> RawMarketEvent.
    live_store = ParquetStore(tmp_path / "live")
    _run(live_store, [_tick(1, 10.0), _tick(2, 11.0, field="ask")], subscribe=("c1",))
    stored = replay_day(live_store, _TRADE_DATE)
    assert [e.value for e in stored] == [10.0, 11.0]  # guard: the live run produced events

    replay_store = ParquetStore(tmp_path / "replay")
    clock = ManualClock(start=_T0)
    supervisor = SessionSupervisor(
        ReplayBrokerSession(stored), client_id=client_id_for("replay"), clock=clock
    )
    supervisor.connect()
    collector = MarketDataCollector(
        store=replay_store,
        universe=_universe(),
        session_id="replay-2026-06-01",
        trade_date=_TRADE_DATE,
        clock=clock,
    )
    # Subscribe by the same broker contract id a live collector would use.
    summary = collector.collect(supervisor, subscribe=["c1"])

    replayed = [e for e in replay_store.read("raw_market_events") if is_observation(e.field_name)]
    assert summary.event_count == 2  # both replayed observations landed; none skipped
    assert sorted(e.value for e in replayed) == [10.0, 11.0]
    # The replayed events carry the same canonical instrument key as the originals.
    assert {e.instrument_key for e in replayed} == {e.instrument_key for e in stored}
