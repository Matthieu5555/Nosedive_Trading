"""Actor-level unit coverage: pipeline shape, determinism, reordering, edges.

The actor (`actor.run_analytics`/`run_day`/`persist_outputs`) is the math-free driver
that runs C's and D's pure functions over an event stream and stamps the outputs. It
holds no economics of its own, so these tests pin its *wiring*, not the numbers C and
D already test: that the pipeline produces every output in the right order, that the
result is a pure function of its inputs (determinism) and invariant to event/position
order (the property that makes same-code-path replay possible), that the edge inputs
yield well-formed empty results rather than partial objects or crashes, that
persistence round-trips through A's store, and that `run_day` reads the raw layer and
binds the correlation id.

The headline same-code-path replay test and the provenance-verification test are
owned by the workstream lead and live elsewhere; these are the unit floor beneath them.

Inputs come from the named fixture library (`fixtures.library`, `fixtures.positions`)
rather than inline literals, per TESTING.md.
"""

from __future__ import annotations

import random
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from actor import ActorOutputs, run_analytics, run_day
from config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from contracts import InstrumentMaster, Position, RawMarketEvent
from contracts.instrument_key import InstrumentKey
from fixtures.events import event, quote_events
from fixtures.library import ChainFixture, get_fixture
from storage import ParquetStore

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
CONFIG_HASH = "cfg-hash-actor"


def _config(*, spot_shocks: tuple[float, ...] = (-0.05, 0.05),
            vol_shocks: tuple[float, ...] = (0.05, -0.05)) -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", underlyings=("AAPL",), exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        scenario=ScenarioConfig(version="scn-1", spot_shocks=spot_shocks, vol_shocks=vol_shocks),
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
    """Turn a named chain fixture into the (events, instruments, masters) the actor needs.

    The underlying gets a tight two-sided quote at its fixture spot; each option gets
    its fixture bid/ask/last. Instruments and masters cover every key so the join can
    resolve a held contract.
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
        # A per-instrument session id keeps (session_id, event_id) unique across the
        # chain so the events are a valid append-only raw batch (replay seeds them).
        events += list(
            quote_events(
                quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last, ts=AS_OF,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument))
    return events, instruments, masters


def _call_options(chain: ChainFixture) -> list[InstrumentKey]:
    return [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]


def _positions(contracts: list[InstrumentKey], quantities: list[float]) -> list[Position]:
    return [
        Position(valuation_ts=AS_OF, portfolio_id="pf-actor", contract_key=c.canonical(),
                 quantity=q, source="record")
        for c, q in zip(contracts, quantities, strict=True)
    ]


def _run(
    events: list[RawMarketEvent],
    positions: list[Position],
    inst: list[InstrumentKey],
    masters: list[InstrumentMaster],
    config: PlatformConfig | None = None,
) -> ActorOutputs:
    return run_analytics(
        events, positions, instruments=inst, masters=masters,
        config=config or _config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )


# --------------------------------------------------------------------------- #
# Pipeline shape: a liquid chain produces every output in the documented order #
# --------------------------------------------------------------------------- #
def test_full_pipeline_produces_every_output_from_a_liquid_chain() -> None:
    chain = get_fixture("synthetic_known_answer")  # 5 strikes, both rights, clean
    events, inst, masters = _chain_inputs(chain)
    calls = _call_options(chain)
    positions = _positions(calls[:3], [10.0, -5.0, 3.0])

    out = _run(events, positions, inst, masters)

    # 11 snapshots: one underlying + ten options.
    assert len(out.snapshots) == 11
    assert len(out.forwards) == 1  # one (underlying, maturity)
    assert len(out.iv_points) == 10  # every option quote converges
    assert len(out.surface_parameters) == 1
    assert len(out.surface_grid) == 5  # one cell per default moneyness bucket
    assert len(out.pricings) == 3  # one per netted position line
    assert len(out.risk_aggregates) == 1  # net per underlying (one underlying)
    # scenarios: one cell per (line, scenario); the default grid has 6 scenarios.
    assert len(out.scenarios) == 3 * 6
    assert not out.is_empty()


def test_output_tuples_carry_provenance_stamps() -> None:
    # Unit-level provenance presence (the cross-cutting walk is the lead's test).
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [10.0])
    out = _run(events, positions, inst, masters)
    stamp_hashes = [
        *(r.provenance.stamp_hash for r in out.snapshots),
        *(r.provenance.stamp_hash for r in out.forwards),
        *(r.provenance.stamp_hash for r in out.iv_points),
        *(r.provenance.stamp_hash for r in out.surface_parameters),
        *(r.provenance.stamp_hash for r in out.surface_grid),
        *(r.provenance.stamp_hash for r in out.pricings),
        *(r.provenance.stamp_hash for r in out.risk_aggregates),
        *(r.provenance.stamp_hash for r in out.scenarios),
    ]
    assert stamp_hashes  # there is at least one persisted row
    assert all(stamp_hashes)  # every row carries a non-empty provenance hash


# --------------------------------------------------------------------------- #
# Determinism and reordering invariance — the replay-enabling properties       #
# --------------------------------------------------------------------------- #
def test_same_inputs_yield_equal_outputs() -> None:
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:3], [10.0, -5.0, 3.0])
    assert _run(events, positions, inst, masters) == _run(events, positions, inst, masters)


def test_shuffling_events_does_not_change_outputs() -> None:
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:3], [10.0, -5.0, 3.0])
    shuffled = events[:]
    random.Random(20260529).shuffle(shuffled)
    assert _run(events, positions, inst, masters) == _run(shuffled, positions, inst, masters)


def test_shuffling_positions_does_not_change_outputs() -> None:
    # net_lots and the pure functions make line/cell ordering input-set-pure (ADR 0006).
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:3], [10.0, -5.0, 3.0])
    reversed_positions = list(reversed(positions))
    assert _run(events, positions, inst, masters) == _run(events, reversed_positions, inst, masters)


def test_duplicate_lots_of_one_contract_net_and_stay_order_independent() -> None:
    # Two lots of one contract; reordering them must not move any output.
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    call = _call_options(chain)[0]
    lots = _positions([call, call], [7.0, -2.0])
    out = _run(events, lots, inst, masters)
    out_rev = _run(events, list(reversed(lots)), inst, masters)
    assert out == out_rev
    # One netted line -> one pricing row, regardless of two lots.
    assert len(out.pricings) == 1


# --------------------------------------------------------------------------- #
# Edge-case floor: empty / no-positions / degenerate slice                     #
# --------------------------------------------------------------------------- #
def test_empty_events_yield_an_empty_well_formed_result() -> None:
    out = run_analytics(
        [], [], instruments=[], masters=[], config=_config(), config_hash=CONFIG_HASH,
        as_of=AS_OF, calc_ts=CALC_TS,
    )
    assert out == ActorOutputs()  # all-empty, not a partial object
    assert out.is_empty()


def test_no_positions_yields_empty_risk_but_full_analytics() -> None:
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    out = _run(events, [], inst, masters)
    # Market analytics are present; the risk/scenario/pricing tuples are empty tuples.
    assert out.snapshots and out.forwards and out.iv_points and out.surface_parameters
    assert out.pricings == ()
    assert out.risk_aggregates == ()
    assert out.scenarios == ()


def test_one_strike_maturity_has_no_forward_and_no_silent_skip() -> None:
    # The single-strike fixture has one call and no put, so no call/put pair can be
    # formed: no forward is recovered for the maturity. With no positions the run is
    # well-formed (no forward, no surface params for the insufficient SVI slice)...
    from actor import ValuationJoinError

    chain = get_fixture("single_strike_maturity")
    events, inst, masters = _chain_inputs(chain)
    no_pos = _run(events, [], inst, masters)
    assert no_pos.forwards == ()
    assert no_pos.surface_parameters == ()  # one strike is below the SVI minimum

    # ...and a held contract on that maturity raises a named join error (no forward),
    # never a silent NaN or a dropped line.
    only_call = _call_options(chain)[0]
    with pytest.raises(ValuationJoinError) as info:
        _run(events, _positions([only_call], [4.0]), inst, masters)
    assert info.value.contract_key == only_call.canonical()
    assert "forward" in info.value.reason or "slice" in info.value.reason


def test_empty_scenario_config_keeps_only_the_d_owned_roll_down_scenario() -> None:
    # An empty config still yields D's one construction-rule scenario (the roll-down,
    # ADR 0006 decision 5), so a single line produces exactly one scenario cell.
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [10.0])
    out = _run(events, positions, inst, masters, config=_config(spot_shocks=(), vol_shocks=()))
    assert out.risk_aggregates  # risk still computed
    assert len(out.scenarios) == 1
    assert out.scenarios[0].scenario_id == "roll_1d"


# --------------------------------------------------------------------------- #
# run_day: reads the raw layer, feeds run_analytics, persists, binds the id    #
# --------------------------------------------------------------------------- #
def _seed_raw_layer(store: ParquetStore, events: list[RawMarketEvent]) -> None:
    store.write("raw_market_events", events)


def test_run_day_replays_the_raw_layer_and_persists(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:3], [10.0, -5.0, 3.0])

    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)

    out = run_day(
        store, AS_OF.date(), positions, instruments=inst, masters=masters, config=_config(),
        config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, correlation_id="sess-1",
        persist=True,
    )
    # run_day's result equals the in-memory run over the same events (one code path).
    assert out == _run(events, positions, inst, masters)

    # The derived rows landed in A's store and read back equal.
    assert store.read("market_state_snapshots") == list(out.snapshots)
    assert store.read("risk_aggregates") == list(out.risk_aggregates)
    assert store.read("scenario_results") == list(out.scenarios)


def test_run_day_without_persist_writes_nothing(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [10.0])

    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)

    run_day(
        store, AS_OF.date(), positions, instruments=inst, masters=masters, config=_config(),
        config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, persist=False,
    )
    assert store.read("market_state_snapshots") == []


def test_persist_is_idempotent_for_fixed_outputs(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [10.0])
    out = _run(events, positions, inst, masters)

    store = ParquetStore(tmp_path)
    from actor import persist_outputs

    persist_outputs(store, out)
    first = store.read("iv_points")
    persist_outputs(store, out)  # writing the same outputs again
    assert store.read("iv_points") == first  # replace-semantics: no duplication


def test_run_day_binds_correlation_id(tmp_path: Path) -> None:
    # The correlation id must reach the structured log line linking the analytics run
    # to its collector session. Capture structlog output and assert it is bound.
    import structlog

    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)

    captured: list[dict[str, object]] = []

    def _capture(
        _logger: object, _name: str, event_dict: MutableMapping[str, object]
    ) -> MutableMapping[str, object]:  # structlog processor
        captured.append(dict(event_dict))
        raise structlog.DropEvent  # capture only; never hit the renderer

    structlog.configure(processors=[_capture])
    try:
        run_day(
            store, AS_OF.date(), [], instruments=inst, masters=masters, config=_config(),
            config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, correlation_id="sess-corr-7",
            persist=False,
        )
    finally:
        structlog.reset_defaults()

    start_lines = [line for line in captured if line.get("event") == "actor.run_day.start"]
    assert start_lines, "run_day emitted no start log line"
    assert start_lines[0]["correlation_id"] == "sess-corr-7"


# --------------------------------------------------------------------------- #
# A skipped quote stays out of the usable set but is still snapshotted          #
# --------------------------------------------------------------------------- #
def test_a_gap_meta_event_is_ignored_before_snapshots() -> None:
    # A reserved __-prefixed meta-event (a gap) is data about absence, not a quote, and
    # must not feed the snapshot builder. Adding one must not change the outputs.
    chain = get_fixture("synthetic_known_answer")
    events, inst, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [10.0])
    baseline = _run(events, positions, inst, masters)

    gap = event(chain.underlying, "__gap", 0.0, ts=AS_OF, event_id="gap-1")
    with_gap = _run([*events, gap], positions, inst, masters)
    assert with_gap == baseline
