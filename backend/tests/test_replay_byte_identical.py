"""Headline: same-code-path replay is byte-identical (live stream vs replay off disk).

This is the test the whole architecture exists to pass — it is not a smoke check.
The actor drives C's and D's pure functions over an event stream; because the
*same* ``run_analytics`` runs whether the events arrive as a live stream or are read
back off the immutable raw layer, the derived outputs (snapshots, forwards, IV
points, surfaces, pricing, risk, scenarios) must come out identical. A separate
"historical only" path is exactly what this test is built to forbid: dual paths
drift, and the drift would show up here.

We assert byte-identity two ways, weakest-to-strongest:
1. the in-memory ``ActorOutputs`` of the live and the replay run compare equal —
   structural ``==`` over frozen dataclasses, every derived contract;
2. the *persisted* Parquet partitions are byte-for-byte identical on disk.

``as_of``/``calc_ts`` are injected (nothing reads a clock), so the only difference
between the two runs is the event *source*. It must not change the result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from actor import ActorOutputs, persist_outputs, run_analytics, run_day
from config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from contracts import (
    InstrumentMaster,
    Position,
    RawMarketEvent,
    table_for_contract,
)
from contracts.instrument_key import InstrumentKey
from fixtures.events import quote_events
from fixtures.library import ChainFixture, get_fixture
from storage import ParquetStore
from storage.partitioning import table_dir

# Injected times shared by both runs: the only knobs that move a stamp, so holding
# them fixed isolates the event source as the single variable under test.
AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
CONFIG_HASH = "cfg-hash-replay"


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", underlyings=("AAPL",), exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
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
    """A named chain fixture as the (events, instruments, masters) the actor consumes.

    Per-instrument session ids keep ``(session_id, event_id)`` unique so the events
    are a valid append-only raw batch that the replay path can seed and read back.
    """
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


# The named liquid chains span three distinct underlyings; combining them is how a
# multi-underlying day — and therefore a multi-partition derived layout — is built.
_MULTI_CHAINS = ("liquid_aapl", "liquid_msft", "liquid_spy")


def _multi_chain_inputs(
    names: tuple[str, ...],
) -> tuple[list[RawMarketEvent], list[InstrumentKey], list[InstrumentMaster]]:
    """Several named chains merged into one day's (events, instruments, masters).

    Per-instrument session ids already keep ``(session_id, event_id)`` unique within
    each chain, and the chains hold disjoint underlyings, so concatenating them is a
    valid append-only raw batch spanning more than one partition.
    """
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
    """One long and one short call in each named chain's underlying.

    Spreading positions over more than one underlying is what makes pricing, risk and
    scenarios land for several underlyings too, so byte-identity is asserted on those
    partitioned families and not only on the per-underlying market-data ones.
    """
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
    """The distinct underlyings a table partitioned on, read off its partition keys."""
    return {underlying for _trade_date, underlying in store.list_partitions(table)}


def _partition_bytes(store: ParquetStore, table: str) -> dict[str, bytes]:
    """Every Parquet file of a table, keyed by its path relative to the table dir.

    Reading the raw bytes (not the decoded records) is what makes "byte-identical"
    a literal claim about what landed on disk, not just about equal values.
    """
    base = table_dir(store.root, table)
    if not base.exists():
        return {}
    return {
        str(path.relative_to(base)): path.read_bytes()
        for path in sorted(base.glob("**/*.parquet"))
    }


def _derived_tables(outputs: ActorOutputs) -> list[str]:
    """The derived tables a non-empty run lands in, in output order."""
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

    # Live path: events arrive as an in-memory stream and are computed directly.
    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )

    # Replay path: the same events are written to the immutable raw layer, then
    # run_day reads them back (collectors.replay_day) and runs the identical compute.
    replay_store = ParquetStore(tmp_path / "replay")
    replay_store.write("raw_market_events", events)
    replay = run_day(
        replay_store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
        correlation_id="replay-session", persist=True,
    )

    # The whole point: one code path, two sources, identical derived outputs.
    assert replay == live
    assert not live.is_empty()
    # Sanity that the run is rich enough to be a real test, not a vacuous equality.
    assert _derived_tables(live) == [
        "market_state_snapshots", "forward_curve", "iv_points",
        "surface_parameters", "surface_grid", "pricing_results",
        "risk_aggregates", "scenario_results",
    ]


def test_persisted_partitions_are_byte_for_byte_identical(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(chain)

    # Live: compute then persist into its own store.
    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )
    live_store = ParquetStore(tmp_path / "live")
    persist_outputs(live_store, live)

    # Replay: seed the raw layer and let run_day compute-and-persist.
    replay_store = ParquetStore(tmp_path / "replay")
    replay_store.write("raw_market_events", events)
    run_day(
        replay_store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
        persist=True,
    )

    # Every derived partition's bytes match between the live and the replay store.
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
    # Replay reads the raw layer in canonical order regardless of the order events
    # were captured, so seeding the raw layer in a shuffled order must yield the same
    # outputs as the live in-arrival-order run — the reordering invariance that makes
    # one-code-path replay safe even when capture order and replay order differ.
    import random

    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(chain)

    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )

    shuffled = events[:]
    random.Random(seed).shuffle(shuffled)
    store = ParquetStore(tmp_path / f"replay-{seed}")
    store.write("raw_market_events", shuffled)
    replay = run_day(
        store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, persist=False,
    )
    assert replay == live


def test_multi_underlying_day_is_byte_identical_across_every_partition(tmp_path: Path) -> None:
    # The single-underlying tests above prove byte-identity for one partition per table.
    # The condition most likely to break it is more than one partition: cross-partition
    # ordering, per-underlying stamping, and the partition layout itself only get
    # exercised when a day spans several underlyings. Drive a three-underlying day live
    # vs. replayed-off-disk and assert the outputs and the on-disk bytes match across
    # every partition of every derived table.
    events, instruments, masters = _multi_chain_inputs(_MULTI_CHAINS)
    positions = _multi_positions(_MULTI_CHAINS)

    live = run_analytics(
        events, positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )
    live_store = ParquetStore(tmp_path / "live")
    persist_outputs(live_store, live)

    replay_store = ParquetStore(tmp_path / "replay")
    replay_store.write("raw_market_events", events)
    replay = run_day(
        replay_store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, persist=True,
    )

    # Values agree, and the run is genuinely multi-underlying — every per-underlying
    # market-data family landed all three, so this is not a single-partition test in
    # disguise.
    assert replay == live
    assert not live.is_empty()
    for table in ("market_state_snapshots", "forward_curve", "iv_points",
                  "surface_parameters", "surface_grid"):
        assert _underlyings_in_partitions(live_store, table) == {"AAPL", "MSFT", "SPY"}, (
            f"{table}: expected all three underlyings to partition"
        )

    # Bytes match across every partition of every derived table — the multi-partition
    # form of the byte-identity guarantee.
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
