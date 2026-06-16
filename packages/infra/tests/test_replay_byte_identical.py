from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.actor import ActorOutputs, persist_outputs, run_analytics, run_day
from algotrading.infra.contracts import (
    InstrumentMaster,
    Position,
    RawMarketEvent,
    table_for_contract,
)
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import table_dir
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, ChainFixture, get_fixture

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
CONFIG_HASH = {"cfg": "cfg-hash-replay"}


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(
            version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
        ),
    )


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=AS_OF.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _chain_inputs(
    chain: ChainFixture,
) -> tuple[list[RawMarketEvent], list[InstrumentKey], list[InstrumentMaster]]:
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot, ts=AS_OF,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain.underlying)]
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last, ts=AS_OF,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument))
    return events, instruments, masters


def _positions(chain: ChainFixture) -> list[Position]:
    calls = [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]
    return [
        Position(valuation_ts=AS_OF, portfolio_id="pf-replay", contract_key=c.canonical(),
                 quantity=q, source="record")
        for c, q in zip(calls[:3], [10.0, -5.0, 3.0], strict=True)
    ]


_MULTI_CHAINS = ("liquid_aapl", "liquid_msft", "liquid_spy")


def _multi_chain_inputs(
    names: tuple[str, ...],
) -> tuple[list[RawMarketEvent], list[InstrumentKey], list[InstrumentMaster]]:
    events: list[RawMarketEvent] = []
    instruments: list[InstrumentKey] = []
    masters: list[InstrumentMaster] = []
    for name in names:
        chain_events, chain_instruments, chain_masters = _chain_inputs(get_fixture(name))
        events += chain_events
        instruments += chain_instruments
        masters += chain_masters
    return events, instruments, masters


def _multi_positions(names: tuple[str, ...]) -> list[Position]:
    positions: list[Position] = []
    for name in names:
        chain = get_fixture(name)
        calls = [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]
        positions += [
            Position(valuation_ts=AS_OF, portfolio_id="pf-replay", contract_key=c.canonical(),
                     quantity=q, source="record")
            for c, q in zip(calls[:2], [7.0, -4.0], strict=True)
        ]
    return positions


def _underlyings_in_partitions(store: ParquetStore, table: str) -> set[str]:
    return {underlying for _trade_date, underlying in store.list_partitions(table)}


def _partition_bytes(store: ParquetStore, table: str) -> dict[str, bytes]:
    base = table_dir(store.root, table)
    if not base.exists():
        return {}
    return {
        str(path.relative_to(base)): path.read_bytes()
        for path in sorted(base.glob("**/*.parquet"))
    }


def _derived_tables(outputs: ActorOutputs) -> list[str]:
    tables: list[str] = []
    for tuple_of_records in (
        outputs.snapshots, outputs.forwards, outputs.iv_points,
        outputs.surface_parameters, outputs.surface_grid, outputs.pricings,
        outputs.risk_aggregates, outputs.scenarios,
    ):
        if tuple_of_records:
            tables.append(table_for_contract(type(tuple_of_records[0])))
    return tables


def test_live_and_replay_runs_produce_equal_actor_outputs(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(chain)

    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )

    replay_store = ParquetStore(tmp_path / "replay")
    replay_store.write("raw_market_events", events)
    replay = run_day(
        replay_store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
        correlation_id="replay-session", persist=True,
    )

    assert replay == live
    assert not live.is_empty()
    assert _derived_tables(live) == [
        "market_state_snapshots", "forward_curve", "iv_points",
        "surface_parameters", "surface_grid", "pricing_results",
        "risk_aggregates", "scenario_results",
    ]


def test_persisted_partitions_are_byte_for_byte_identical(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(chain)

    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )
    live_store = ParquetStore(tmp_path / "live")
    persist_outputs(live_store, live)

    replay_store = ParquetStore(tmp_path / "replay")
    replay_store.write("raw_market_events", events)
    run_day(
        replay_store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
        persist=True,
    )

    derived = _derived_tables(live)
    assert derived, "the run must produce derived outputs for this test to mean anything"
    for table in derived:
        live_bytes = _partition_bytes(live_store, table)
        replay_bytes = _partition_bytes(replay_store, table)
        assert live_bytes, f"{table}: live store wrote no partition"
        assert live_bytes.keys() == replay_bytes.keys(), f"{table}: partition layout differs"
        for relative_path, payload in live_bytes.items():
            assert replay_bytes[relative_path] == payload, (
                f"{table}/{relative_path}: replay bytes differ from live"
            )


@pytest.mark.parametrize("seed", [1, 7, 20260529])
def test_event_arrival_order_does_not_change_the_replay_result(tmp_path: Path, seed: int) -> None:
    import random

    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(chain)

    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )

    shuffled = events[:]
    random.Random(seed).shuffle(shuffled)
    store = ParquetStore(tmp_path / f"replay-{seed}")
    store.write("raw_market_events", shuffled)
    replay = run_day(
        store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, persist=False,
    )
    assert replay == live


def test_multi_underlying_day_is_byte_identical_across_every_partition(tmp_path: Path) -> None:
    events, instruments, masters = _multi_chain_inputs(_MULTI_CHAINS)
    positions = _multi_positions(_MULTI_CHAINS)

    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )
    live_store = ParquetStore(tmp_path / "live")
    persist_outputs(live_store, live)

    replay_store = ParquetStore(tmp_path / "replay")
    replay_store.write("raw_market_events", events)
    replay = run_day(
        replay_store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, persist=True,
    )

    assert replay == live
    assert not live.is_empty()
    for table in ("market_state_snapshots", "forward_curve", "iv_points",
                  "surface_parameters", "surface_grid"):
        assert _underlyings_in_partitions(live_store, table) == {"AAPL", "MSFT", "SPY"}, (
            f"{table}: expected all three underlyings to partition"
        )

    derived = _derived_tables(live)
    assert derived
    for table in derived:
        live_bytes = _partition_bytes(live_store, table)
        replay_bytes = _partition_bytes(replay_store, table)
        assert live_bytes, f"{table}: live store wrote no partition"
        assert live_bytes.keys() == replay_bytes.keys(), f"{table}: partition layout differs"
        for relative_path, payload in live_bytes.items():
            assert replay_bytes[relative_path] == payload, (
                f"{table}/{relative_path}: replay bytes differ from live"
            )
