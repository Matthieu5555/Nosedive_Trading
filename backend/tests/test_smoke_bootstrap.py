"""Step-1 smoke bootstrap, the broker-agnostic boundary, and same-code-path replay.

The smoke is the end-to-end bootstrap the spec asks for: resolve one contract, request
one quote, write one event to disk — and place no orders. The boundary tests pin the
architecture's load-bearing bet: the internal event type carries no broker enum, and a
live session and a disk-replay session emit the very same internal type through the
very same supervisor code path — which is what makes E's same-code-path replay possible.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import get_args, get_type_hints

from collectors import MarketDataCollector, is_observation
from connectivity import (
    BrokerSession,
    BrokerTick,
    FakeBrokerSession,
    ManualClock,
    ReplayBrokerSession,
    ScriptItem,
    SessionSupervisor,
    client_id_for,
)
from contracts import RawMarketEvent, broker_contract_id_from_canonical
from storage import ParquetStore
from universe import UniverseService, materialize_universe, resolve_contract_row

_TRADE_DATE = date(2026, 6, 1)
_T0 = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)
_OPT_KEY = "AAPL|OPT|SMART|USD|100|o1|2026-06-19|100|C"

_UNDERLYING_ROW: dict[str, object] = {
    "conId": "u-AAPL", "symbol": "AAPL", "secType": "STK", "exchange": "SMART",
    "currency": "USD", "multiplier": 1,
}
_OPTION_ROW: dict[str, object] = {
    "conId": "o-AAPL-C-100", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
    "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "C",
}


# -- step-1 smoke ------------------------------------------------------------


def test_step1_bootstrap_resolves_one_contract_and_writes_one_quote(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    clock = ManualClock(start=_T0)
    one_quote: list[ScriptItem] = [
        BrokerTick("o-AAPL-C-100", "bid", 5.25, sequence=1, exchange_ts=_T0 + timedelta(seconds=1))
    ]
    session = FakeBrokerSession(chains={"AAPL": (_UNDERLYING_ROW, _OPTION_ROW)}, script=one_quote)
    supervisor = SessionSupervisor(session, client_id=client_id_for("smoke"), clock=clock)
    supervisor.connect()

    # Discover and materialize the universe, then resolve exactly one contract.
    rows = supervisor.request_option_chain("AAPL")
    materialize_universe(store, rows, _TRADE_DATE)
    universe = UniverseService.load_active_universe(store, _TRADE_DATE)
    option = universe.get_option_chain("AAPL", _TRADE_DATE)[0]

    # Request one quote and capture exactly one event.
    collector = MarketDataCollector(
        store=store,
        universe=universe,
        session_id="smoke-bootstrap",
        trade_date=_TRADE_DATE,
        clock=clock,
    )
    summary = collector.collect(supervisor, subscribe=[option.broker_contract_id])

    events = store.read("raw_market_events")
    assert len(events) == 1
    assert events[0].field_name == "bid"
    assert events[0].value == 5.25
    assert events[0].instrument_key == option.canonical()
    assert summary.event_count == 1
    # No orders were placed: nothing was written to the positions layer.
    assert store.list_partitions("positions") == []


# -- broker-agnostic boundary ------------------------------------------------


def test_broker_tick_exposes_no_broker_enum() -> None:
    # Every field of the internal tick type is a plain scalar; no Enum (broker tick-type
    # enums are mapped to the string field_name inside the adapter), no broker SDK type.
    allowed = {str, int, float, datetime, type(None)}
    for name, hint in get_type_hints(BrokerTick).items():
        member_types = set(get_args(hint)) or {hint}
        assert member_types <= allowed, f"{name} has a non-scalar type {hint!r}"
        for member in member_types:
            assert not (isinstance(member, type) and issubclass(member, Enum)), name


def test_broker_contract_id_round_trips_through_the_canonical_key() -> None:
    # Replay recovers a tick's broker contract id from the canonical instrument key. The
    # broker-id slot is stored verbatim, so it round-trips exactly — the property that
    # lets a replayed tick resolve against the universe. Independent oracle: the conId in.
    for row in (_UNDERLYING_ROW, _OPTION_ROW):
        key = resolve_contract_row(row)
        assert key.broker_contract_id == row["conId"]
        assert broker_contract_id_from_canonical(key.canonical()) == key.broker_contract_id


def test_live_and_replay_sessions_emit_the_same_internal_tick_type() -> None:
    live = FakeBrokerSession(script=[BrokerTick("o1", "bid", 1.0, sequence=1)])
    live.connect(client_id_for("smoke"))
    replayed_event = RawMarketEvent(
        session_id="s", event_id="e", instrument_key=_OPT_KEY,
        exchange_ts=_T0, receipt_ts=_T0, canonical_ts=_T0, field_name="bid", value=1.0,
        trade_date=_TRADE_DATE, underlying="AAPL",
    )
    replay = ReplayBrokerSession([replayed_event])
    replay.connect(client_id_for("replay"))

    live_tick = next(iter(live.ticks()))
    replay_tick = next(iter(replay.ticks()))
    assert type(live_tick) is type(replay_tick) is BrokerTick
    # Both are interchangeable through the broker-agnostic Protocol.
    assert isinstance(live, BrokerSession)
    assert isinstance(replay, BrokerSession)


def test_the_supervisor_streams_replayed_ticks_through_the_same_code_path() -> None:
    # The same SessionSupervisor.stream() the live collector uses runs unchanged over a
    # disk-replay session — the seam that lets E replay without a broker.
    events = [
        RawMarketEvent(
            session_id="s", event_id=f"e{n}", instrument_key=_OPT_KEY,
            exchange_ts=_T0 + timedelta(seconds=n), receipt_ts=_T0 + timedelta(seconds=n),
            canonical_ts=_T0 + timedelta(seconds=n), field_name="bid", value=float(n),
            trade_date=_TRADE_DATE, underlying="AAPL",
        )
        for n in (1, 2, 3)
    ]
    supervisor = SessionSupervisor(
        ReplayBrokerSession(events), client_id=client_id_for("replay"), clock=ManualClock()
    )
    supervisor.connect()
    streamed = [item.tick for item in supervisor.stream()]
    assert all(isinstance(tick, BrokerTick) for tick in streamed)
    assert [tick.value for tick in streamed] == [1.0, 2.0, 3.0]
    # The replayed tick carries the broker contract id recovered from the canonical key
    # (the "o1" slot of _OPT_KEY), not the canonical key itself — so the collector can
    # resolve it against the universe exactly as it would a live tick.
    assert {tick.broker_contract_id for tick in streamed} == {"o1"}
    assert all(is_observation(event.field_name) for event in events)
