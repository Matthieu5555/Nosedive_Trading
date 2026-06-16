from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import algotrading.infra.storage.adapter as adapter_module
import pytest
from algotrading.infra.collectors import (
    GAP_FIELD,
    BrokerTick,
    FeedFault,
    RawCollector,
    ReplaySource,
    SequenceStamping,
    is_observation,
)
from algotrading.infra.connectivity import GapInterval
from algotrading.infra.contracts import RawMarketEvent
from algotrading.infra.storage import ParquetStore

_TRADE_DATE = date(2026, 6, 1)
_SESSION = "sess-2026-06-01"
_T0 = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)
_KEY = "OPT:BTC:OPT:20251226:C:100000:1:DERIBIT:USD"
_KEY2 = "OPT:BTC:OPT:20251226:P:100000:1:DERIBIT:USD"


class _FixedClock:
    def __init__(self, start: datetime = _T0) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now


class _PushAdapter:

    def __init__(self) -> None:
        self.tick_cb = None
        self.fault_cb = None
        self.subscribed: list[str] = []
        self.unsubscribed = False

    def subscribe(self, instrument_keys: Sequence[str]) -> None:
        self.subscribed.extend(instrument_keys)

    def set_tick_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self.tick_cb = callback

    def set_fault_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self.fault_cb = callback

    def unsubscribe_all(self) -> None:
        self.unsubscribed = True


def _tick(sequence: int, value: float, *, key: str = _KEY, field: str = "bid") -> BrokerTick:
    return BrokerTick(
        instrument_key=key,
        field_name=field,
        value=value,
        underlying="BTC",
        sequence=sequence,
        exchange_ts=_T0 + timedelta(seconds=sequence),
    )


def _live_stream(values: Sequence[tuple[float, str]]) -> list[BrokerTick]:
    from algotrading.infra.collectors import next_sequence

    counters: dict[tuple[str, str], int] = {}
    ticks: list[BrokerTick] = []
    for index, (value, field) in enumerate(values):
        ticks.append(
            BrokerTick(
                instrument_key=_KEY,
                field_name=field,
                value=value,
                underlying="BTC",
                sequence=next_sequence(counters, _KEY, field),
                exchange_ts=_T0 + timedelta(seconds=index),
            )
        )
    return ticks


def _collector(
    store: ParquetStore, adapter: _PushAdapter, *, flush_batch_size: int = 256
) -> RawCollector:
    return RawCollector(
        store=store,
        adapter=adapter,
        session_id=_SESSION,
        trade_date=_TRADE_DATE,
        clock=_FixedClock(),
        subscribed_keys=(_KEY,),
        flush_batch_size=flush_batch_size,
    )


def _feed(collector: RawCollector, adapter: _PushAdapter, ticks: Sequence[BrokerTick]) -> None:
    for tick in ticks:
        adapter.tick_cb(tick)  # type: ignore[misc]
    collector.flush()


def _events(store: ParquetStore) -> list[RawMarketEvent]:
    return store.read("raw_market_events")


def test_collected_ticks_are_persisted_as_raw_events(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = _collector(store, adapter)
    _feed(collector, adapter, [_tick(0, 10.0), _tick(0, 11.0, field="ask")])
    events = sorted(_events(store), key=lambda e: e.field_name)
    assert [e.field_name for e in events] == ["ask", "bid"]
    assert sorted(e.value for e in events) == [10.0, 11.0]
    assert all(e.session_id == _SESSION for e in events)
    assert all(e.instrument_key == _KEY for e in events)


def test_redelivered_tick_within_a_session_is_not_double_written(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = _collector(store, adapter)
    _feed(collector, adapter, [_tick(0, 10.0), _tick(1, 11.0), _tick(1, 11.0), _tick(2, 12.0)])
    events = _events(store)
    assert len({e.event_id for e in events}) == 3
    assert len(events) == 3


def test_kill_and_restart_writes_each_event_exactly_once(tmp_path: Path) -> None:
    first_store = ParquetStore(tmp_path)
    first_adapter = _PushAdapter()
    first = _collector(first_store, first_adapter)
    _feed(first, first_adapter, [_tick(0, 10.0), _tick(1, 11.0)])
    assert len(_events(first_store)) == 2

    restart_store = ParquetStore(tmp_path)
    restart_adapter = _PushAdapter()
    restart = _collector(restart_store, restart_adapter)
    _feed(restart, restart_adapter, [_tick(0, 10.0), _tick(1, 11.0), _tick(2, 12.0)])

    events = _events(restart_store)
    assert len(events) == 3
    assert len({(e.session_id, e.event_id) for e in events}) == 3
    assert sorted(e.value for e in events) == [10.0, 11.0, 12.0]


def test_a_failed_flush_leaves_no_partial_record_then_restart_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"fail_on": 3, "count": 0}
    real_write_table = adapter_module.pq.write_table

    def flaky_write_table(*args: object, **kwargs: object) -> None:
        state["count"] += 1
        if state["count"] == state["fail_on"]:
            raise OSError("disk full (injected)")
        real_write_table(*args, **kwargs)

    monkeypatch.setattr(adapter_module.pq, "write_table", flaky_write_table)

    crash_store = ParquetStore(tmp_path)
    crash_adapter = _PushAdapter()
    _collector(crash_store, crash_adapter, flush_batch_size=1)
    with pytest.raises(OSError, match="injected"):
        for value, seq in [(10.0, 0), (11.0, 1), (12.0, 2)]:
            crash_adapter.tick_cb(_tick(seq, value))  # type: ignore[misc]

    assert sorted(e.value for e in _events(crash_store)) == [10.0, 11.0]

    state["fail_on"] = -1
    restart_store = ParquetStore(tmp_path)
    restart_adapter = _PushAdapter()
    restart = _collector(restart_store, restart_adapter, flush_batch_size=1)
    _feed(restart, restart_adapter, [_tick(0, 10.0), _tick(1, 11.0), _tick(2, 12.0)])

    assert sorted(e.value for e in _events(restart_store)) == [10.0, 11.0, 12.0]


def test_absent_value_tick_is_not_stored(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = _collector(store, adapter)
    absent = BrokerTick(instrument_key=_KEY, field_name="bid", value=None, underlying="BTC")
    nonfinite = BrokerTick(
        instrument_key=_KEY, field_name="ask", value=float("nan"), underlying="BTC"
    )
    _feed(collector, adapter, [absent, nonfinite, _tick(0, 10.0)])
    events = _events(store)
    assert len(events) == 1
    assert events[0].value == 10.0


def test_reserved_field_tick_is_skipped_not_stored(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = _collector(store, adapter)
    reserved = BrokerTick(instrument_key=_KEY, field_name=GAP_FIELD, value=1.0, underlying="BTC")
    _feed(collector, adapter, [reserved, _tick(0, 10.0)])
    events = _events(store)
    assert len(events) == 1
    assert events[0].field_name == "bid"


def test_a_reconnect_records_a_gap_event_per_subscribed_instrument(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = RawCollector(
        store=store, adapter=adapter, session_id=_SESSION, trade_date=_TRADE_DATE,
        clock=_FixedClock(), subscribed_keys=(_KEY, _KEY2),
    )
    adapter.tick_cb(_tick(0, 10.0))  # type: ignore[misc]
    gap = GapInterval(started_at=_T0, ended_at=_T0 + timedelta(seconds=3))
    collector.record_reconnect(gap)
    adapter.tick_cb(_tick(1, 11.0))  # type: ignore[misc]
    summary = collector.close()

    gaps = [e for e in _events(store) if e.field_name == GAP_FIELD]
    assert len(gaps) == 2
    assert {e.instrument_key for e in gaps} == {_KEY, _KEY2}
    assert all(e.value == 3.0 for e in gaps)
    assert summary.gap_count == 2
    assert summary.reconnect_count == 1


def test_gap_events_are_idempotent_across_restart(tmp_path: Path) -> None:
    gap = GapInterval(started_at=_T0, ended_at=_T0 + timedelta(seconds=3))

    first_store = ParquetStore(tmp_path)
    first_adapter = _PushAdapter()
    first = _collector(first_store, first_adapter)
    first.record_reconnect(gap)
    gaps_first = [e for e in _events(first_store) if e.field_name == GAP_FIELD]

    restart_store = ParquetStore(tmp_path)
    restart_adapter = _PushAdapter()
    restart = _collector(restart_store, restart_adapter)
    restart.record_reconnect(gap)
    gaps_second = [e for e in _events(restart_store) if e.field_name == GAP_FIELD]

    assert len(gaps_first) == len(gaps_second) == 1


def test_pacing_fault_is_counted_in_the_summary(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = _collector(store, adapter)
    adapter.fault_cb(FeedFault(kind="pacing", code=420, message="pacing violation"))  # type: ignore[misc]
    _feed(collector, adapter, [_tick(0, 10.0)])
    summary = collector.build_summary()
    assert summary.pacing_failures == 1
    assert all(e.field_name != "pacing" for e in _events(store))


def test_replaying_a_captured_day_through_the_collector_writes_nothing_new(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = _collector(store, adapter)
    _feed(collector, adapter, _live_stream([
        (10.0, "bid"), (11.0, "ask"), (12.0, "bid"),
    ]))
    captured = sorted(_events(store), key=lambda e: e.event_id)
    assert len(captured) == 3

    replay_source = ReplaySource(captured)
    replay_collector = RawCollector(
        store=store, adapter=replay_source, session_id=_SESSION, trade_date=_TRADE_DATE,
        clock=_FixedClock(start=_T0 + timedelta(hours=1)), subscribed_keys=(_KEY,),
    )
    replay_source.pump()
    replay_collector.flush()

    after = sorted(_events(store), key=lambda e: e.event_id)
    assert len(after) == 3
    assert [e.event_id for e in after] == [e.event_id for e in captured]


def test_replay_into_a_fresh_store_reproduces_the_same_event_ids(tmp_path: Path) -> None:
    live_store = ParquetStore(tmp_path / "live")
    adapter = _PushAdapter()
    live = _collector(live_store, adapter)
    _feed(live, adapter, _live_stream([(10.0, "bid"), (11.0, "bid"), (5.0, "ask")]))
    captured = sorted(_events(live_store), key=lambda e: e.event_id)

    replay_store = ParquetStore(tmp_path / "replay")
    replay_source = ReplaySource(captured)
    replay = RawCollector(
        store=replay_store, adapter=replay_source, session_id=_SESSION,
        trade_date=_TRADE_DATE, clock=_FixedClock(), subscribed_keys=(_KEY,),
    )
    replay_source.pump()
    replay.flush()

    replayed = sorted(
        (e for e in _events(replay_store) if is_observation(e.field_name)),
        key=lambda e: e.event_id,
    )
    assert [e.event_id for e in replayed] == [e.event_id for e in captured]
    assert [e.value for e in replayed] == [e.value for e in captured]


def test_live_stamping_skips_dropped_ticks_so_replay_ids_match(tmp_path: Path) -> None:
    from algotrading.infra.contracts import content_event_id

    raw_ticks = [
        BrokerTick(instrument_key=_KEY, field_name="bid", value=None, underlying="BTC"),
        BrokerTick(instrument_key=_KEY, field_name="bid", value=10.0, underlying="BTC",
                   exchange_ts=_T0 + timedelta(seconds=1)),
        BrokerTick(instrument_key=_KEY, field_name="bid", value=float("nan"), underlying="BTC"),
        BrokerTick(instrument_key=_KEY, field_name="ask", value="halted", underlying="BTC"),
        BrokerTick(instrument_key=_KEY, field_name="bid", value=12.0, underlying="BTC",
                   exchange_ts=_T0 + timedelta(seconds=2)),
        BrokerTick(instrument_key=_KEY, field_name="ask", value=7.0, underlying="BTC",
                   exchange_ts=_T0 + timedelta(seconds=3)),
    ]
    expected_ids = {
        content_event_id(_KEY, "bid", 0),
        content_event_id(_KEY, "bid", 1),
        content_event_id(_KEY, "ask", 0),
    }

    live_store = ParquetStore(tmp_path / "live")
    broker = _PushAdapter()
    stamping = SequenceStamping(broker)
    live = RawCollector(
        store=live_store, adapter=stamping, session_id=_SESSION, trade_date=_TRADE_DATE,
        clock=_FixedClock(), subscribed_keys=(_KEY,),
    )
    for tick in raw_ticks:
        broker.tick_cb(tick)  # type: ignore[misc]
    live.flush()
    captured = sorted(_events(live_store), key=lambda e: e.event_id)
    assert {e.event_id for e in captured} == expected_ids
    assert sorted(e.value for e in captured) == [7.0, 10.0, 12.0]

    replay_store = ParquetStore(tmp_path / "replay")
    replay_source = ReplaySource(captured)
    replay = RawCollector(
        store=replay_store, adapter=replay_source, session_id=_SESSION,
        trade_date=_TRADE_DATE, clock=_FixedClock(), subscribed_keys=(_KEY,),
    )
    replay_source.pump()
    replay.flush()
    replayed = sorted(
        (e for e in _events(replay_store) if is_observation(e.field_name)),
        key=lambda e: e.event_id,
    )
    assert {e.event_id for e in replayed} == expected_ids
    assert [e.event_id for e in replayed] == [e.event_id for e in captured]
