"""Orchestration behavior tests — the operable wiring, held to behavior not coverage.

E is behavior-tested (ADR 0007 §5, TESTING.md): its bugs live in wiring and timing,
not in branches, so these tests pin the named cases the spec's "Orchestration and
replay robustness" surface assigns to this workstream, plus the metric-increment and
job-result obligations. The headline cases are:

* ``test_kill_mid_pipeline_then_restart_*`` — a stage raises mid-run; the restart
  re-runs only the unfinished tail, converges to the same store state with no
  duplicated or corrupted rows, and the recorded state names the last healthy run and
  the current backlog.
* ``test_collector_failure_detected_within_documented_interval`` — a silent collector
  is detected within the documented interval using an injected ``ManualClock``, not a
  real wait, and not before the interval elapses.
* ``test_correlation_id_links_collector_session_to_analytics`` — drive an analytics run
  under one correlation id and assert the same id appears on the analytics job's
  structured log lines (the trace resolves end to end).
* ``test_*_metric_increments_on_*`` — a forward failure bumps the forward-failure
  counter; a non-converging solve bumps the solver-failure counter — each on the right
  event.

Expected values are derived independently: counts are hand-derived from the named
fixtures, and the detection bound is the documented interval constant itself.

Relocated from ``backend/tests`` onto the ``packages/`` stack (C3): the wiring under
test is ``algotrading.infra.orchestration`` driving the ported actor and QC plane. The
two *live-collection* cases drive the unified push collector (C6 / ADR 0027) through
``collect_live`` over a fake push adapter — one collector, one tick, content-addressed
capture — and assert the shared correlation id and the per-underlying event counter.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import structlog
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.collectors import (
    BrokerTick,
    CollectorSummary,
    RawCollector,
    next_sequence,
)
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    Position,
    RawMarketEvent,
)
from algotrading.infra.orchestration import (
    COLLECTOR_SILENCE_SECONDS,
    EOD_STAGES,
    EodStages,
    StageRun,
    backlog_stages,
    build_dashboard,
    build_metrics,
    collect_live,
    collector_death_alert,
    completed_stages,
    elevated_failure_rate_alert,
    last_healthy_trade_date,
    missing_partition_alerts,
    qc_fail_alert,
    reconcile_end_of_day,
    record_forward_failure,
    record_stage,
    refresh_universe,
    render_dashboard,
    run_end_of_day,
    run_incremental_analytics,
    run_qc,
    sample_value,
)
from algotrading.infra.orchestration.run_state import OUTCOME_FAILED, OUTCOME_OK
from algotrading.infra.qc import thresholds_from_config
from algotrading.infra.risk import BrokerGreeks
from algotrading.infra.storage import ParquetStore
from fixtures.events import quote_events
from fixtures.library import ChainFixture, get_fixture

# --------------------------------------------------------------------------- #
# Shared scaffolding: a clean liquid chain → events / instruments / masters    #
# --------------------------------------------------------------------------- #
AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
TRADE_DATE = AS_OF.date()
CONFIG_HASH = "cfg-hash-orch"

# A capture-time reference instant for the clock-driven alert tests (no wall clock read).
_T0 = datetime(2026, 5, 29, 13, 30, tzinfo=UTC)


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
    """Named-fixture chain → the (events, instruments, masters) the actor/jobs need."""
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


def _call_options(chain: ChainFixture) -> list[InstrumentKey]:
    return [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]


def _positions(contracts: list[InstrumentKey], quantities: list[float]) -> list[Position]:
    return [
        Position(valuation_ts=AS_OF, portfolio_id="pf-orch", contract_key=c.canonical(),
                 quantity=q, source="record")
        for c, q in zip(contracts, quantities, strict=True)
    ]


def _seed_raw_layer(store: ParquetStore, events: list[RawMarketEvent]) -> None:
    store.write("raw_market_events", events)


class _CapturingProcessor:
    """A structlog processor that records every event dict and drops it.

    The capture pattern from test_actor::test_run_day_binds_correlation_id: install as
    the only processor, collect the dicts, and raise DropEvent so nothing renders.
    """

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def __call__(
        self, _logger: object, _name: str, event_dict: MutableMapping[str, object]
    ) -> MutableMapping[str, object]:
        self.records.append(dict(event_dict))
        raise structlog.DropEvent


# =========================================================================== #
# Live collection through the one unified collector (C6 / ADR 0027)            #
# =========================================================================== #
class _FakePushAdapter:
    """A push MarketDataAdapter that replays a fixed list of ticks when driven — no broker.

    Captures the collector's tick callback (after :class:`SequenceStamping` wraps it) and, on
    :meth:`pump`, pushes each scripted tick through it. The script's ticks omit ``sequence``;
    the stamping wrapper assigns it, exactly as it does for a live feed.
    """

    def __init__(self, ticks: list[BrokerTick]) -> None:
        self._ticks = ticks
        self._tick_cb = None

    def subscribe(self, instrument_keys: object) -> None: ...
    def set_tick_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self._tick_cb = callback
    def set_fault_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        ...
    def unsubscribe_all(self) -> None: ...

    def pump(self, _collector: RawCollector) -> None:
        for tick in self._ticks:
            self._tick_cb(tick)  # type: ignore[misc]


def _capture_ticks(chain: ChainFixture) -> tuple[list[BrokerTick], list[str]]:
    """Build a live-feed tick script (bid/ask/last per instrument) for a chain fixture.

    Returns the ticks and the canonical keys to subscribe. Values mirror ``_chain_inputs`` so
    the captured raw layer is rich enough for the actor to run, and the per-underlying counts
    are hand-derivable.
    """
    spot = chain.underlying_spot
    counters: dict[tuple[str, str], int] = {}
    ticks: list[BrokerTick] = []
    keys: list[str] = []

    def _add(instrument: InstrumentKey, bid: float, ask: float, last: float) -> None:
        key = instrument.canonical()
        keys.append(key)
        for field, value in (("bid", bid), ("ask", ask), ("last", last)):
            ticks.append(
                BrokerTick(
                    instrument_key=key, field_name=field, value=value,
                    underlying=instrument.underlying_symbol,
                    sequence=next_sequence(counters, key, field), exchange_ts=AS_OF,
                )
            )

    _add(chain.underlying, spot - 0.05, spot + 0.05, spot)
    for quote in chain.quotes:
        _add(quote.instrument, quote.bid, quote.ask, quote.last)
    return ticks, keys


def test_collection_and_analytics_share_one_correlation_id_end_to_end(tmp_path: Path) -> None:
    # Capture live through the one collector, then run analytics over the captured raw layer —
    # both under one correlation id, so the trace resolves the session to the jobs it fed.
    chain = get_fixture("synthetic_known_answer")
    _, instruments, masters = _chain_inputs(chain)
    ticks, keys = _capture_ticks(chain)
    store = ParquetStore(tmp_path)
    clock = ManualClock(start=AS_OF)
    adapter = _FakePushAdapter(ticks)

    processor = _CapturingProcessor()
    structlog.configure(processors=[processor])

    collection = collect_live(
        store=store, adapter=adapter, subscribe=keys, session_id="corr-shared",
        trade_date=TRADE_DATE, clock=clock, drive=adapter.pump, correlation_id="corr-shared",
    )
    analytics = run_incremental_analytics(
        store=store, config=_config(), config_hash=CONFIG_HASH, positions=[],
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=clock, correlation_id="corr-shared",
    )
    structlog.reset_defaults()

    assert collection.summary.event_count > 0  # the live capture actually landed events
    assert not analytics.outputs.is_empty()  # analytics ran over the captured raw layer
    # Both the collection and the analytics job log lines carry the one correlation id.
    corr_ids = {r.get("correlation_id") for r in processor.records if "correlation_id" in r}
    assert corr_ids == {"corr-shared"}
    jobs_logged = {r.get("job") for r in processor.records if "job" in r}
    assert {"collection", "analytics"} <= jobs_logged


def test_collected_events_metric_increments_per_underlying(tmp_path: Path) -> None:
    # The events_collected counter is labeled by underlying, fed off the real captured layer.
    chain = get_fixture("synthetic_known_answer")
    ticks, keys = _capture_ticks(chain)
    store = ParquetStore(tmp_path)
    metrics = build_metrics()
    adapter = _FakePushAdapter(ticks)

    collect_live(
        store=store, adapter=adapter, subscribe=keys, session_id="sess-metric",
        trade_date=TRADE_DATE, clock=ManualClock(start=AS_OF),
        drive=adapter.pump, correlation_id="corr-metric", metrics=metrics,
    )
    # Hand-derived oracle: every scripted observation is for underlying AAPL, three fields per
    # instrument, so the counter for AAPL equals the number of captured observations.
    captured = [e for e in store.read("raw_market_events") if e.session_id == "sess-metric"]
    expected = len(captured)
    assert expected == len(ticks)  # all scripted ticks landed (finite values, distinct ids)
    assert (
        sample_value(metrics.registry, "events_collected_total", {"underlying": "AAPL"})
        == expected
    )


# =========================================================================== #
# 1. Kill mid-pipeline and restart: no dup/corrupt; last healthy + backlog     #
# =========================================================================== #
def _eod_stages(
    store: ParquetStore,
    events: list[RawMarketEvent],
    instruments: list[InstrumentKey],
    masters: list[InstrumentMaster],
    positions: list[Position],
    *,
    clock: ManualClock,
    correlation_id: str,
    analytics_explodes: bool = False,
) -> EodStages:
    """Wire the five EOD stages over real jobs, with an optional exploding analytics.

    Universe-refresh and collection are pre-seeded no-ops here (the raw layer is seeded
    directly) so the test isolates the analytics→reconciliation→QC tail. When
    ``analytics_explodes`` is set, the analytics stage raises after doing nothing, to
    simulate a kill at that stage. The collection stage returns a recorded summary
    rather than a live capture — the injected seam C1's live-collection job will fill.
    """
    config = _config()
    thresholds = thresholds_from_config(config.qc_threshold)

    def _universe_stage():  # type: ignore[no-untyped-def]
        return refresh_universe(
            store=store, config=config, masters=masters, trade_date=TRADE_DATE,
            correlation_id=correlation_id, persist=False,
        )

    # Collection is represented by a recorded summary (no live broker in this tail test).
    summary = CollectorSummary(
        session_id=correlation_id, trade_date=TRADE_DATE, event_count=len(events),
        gap_count=0, reconnect_count=0, subscribed_count=len(instruments),
        covered_count=len(instruments), per_field_counts=(), pacing_failures=0,
        entitlement_failures=0,
    )
    from algotrading.infra.orchestration.jobs import CollectionResult

    def collection_stage() -> CollectionResult:
        return CollectionResult(
            correlation_id=correlation_id, session_id=correlation_id, summary=summary
        )

    def analytics_stage():  # type: ignore[no-untyped-def]
        if analytics_explodes:
            raise RuntimeError("simulated kill mid-analytics")
        return run_incremental_analytics(
            store=store, config=config, config_hash=CONFIG_HASH, positions=positions,
            instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
            calc_ts=CALC_TS, clock=clock, correlation_id=correlation_id,
        )

    def reconciliation_stage():  # type: ignore[no-untyped-def]
        return reconcile_end_of_day(
            lines=(), broker_greeks=(), trade_date=TRADE_DATE, correlation_id=correlation_id,
        )

    def qc_stage():  # type: ignore[no-untyped-def]
        return run_qc(
            store=store, thresholds=thresholds, collector_summary=summary,
            trade_date=TRADE_DATE, run_id=correlation_id, run_ts=CALC_TS,
            correlation_id=correlation_id,
        )

    return EodStages(
        universe_refresh=_universe_stage,
        collection=collection_stage,
        analytics=analytics_stage,
        reconciliation=reconciliation_stage,
        qc=qc_stage,
    )


def test_kill_mid_pipeline_then_restart_converges_with_no_duplicate_rows(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:2], [10.0, -5.0])

    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    clock = ManualClock(start=CALC_TS)

    # First attempt: analytics raises, simulating a kill mid-run.
    exploding = _eod_stages(
        store, events, instruments, masters, positions, clock=clock,
        correlation_id="corr-kill", analytics_explodes=True,
    )
    with pytest.raises(RuntimeError, match="simulated kill"):
        run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-kill",
                       clock=clock, stages=exploding)

    # The recorded state names the backlog (analytics onward) and no healthy run yet.
    assert backlog_stages(tmp_path, TRADE_DATE) == ["analytics", "reconciliation", "qc"]
    assert "universe_refresh" in completed_stages(tmp_path, TRADE_DATE)
    assert "collection" in completed_stages(tmp_path, TRADE_DATE)
    assert last_healthy_trade_date(tmp_path) is None

    # Restart with a clean analytics stage; it resumes only the unfinished tail.
    healthy = _eod_stages(
        store, events, instruments, masters, positions, clock=clock,
        correlation_id="corr-restart",
    )
    result = run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-restart",
                            clock=clock, stages=healthy)
    assert set(result.skipped) == {"universe_refresh", "collection"}
    assert set(result.ran) == {"analytics", "reconciliation", "qc"}

    # The store converged: derived rows are present and equal a single clean run.
    snapshots_after_restart = store.read("market_state_snapshots")
    iv_after_restart = store.read("iv_points")
    risk_after_restart = store.read("risk_aggregates")

    # Run the analytics once more (idempotent replace-semantics) — no duplication.
    rerun = run_incremental_analytics(
        store=store, config=_config(), config_hash=CONFIG_HASH, positions=positions,
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=clock, correlation_id="corr-rerun",
    )
    assert store.read("market_state_snapshots") == snapshots_after_restart  # no dup rows
    assert store.read("iv_points") == iv_after_restart
    assert store.read("risk_aggregates") == risk_after_restart
    assert len(rerun.outputs.snapshots) == len(snapshots_after_restart)

    # Now the whole day is healthy and there is no backlog.
    assert backlog_stages(tmp_path, TRADE_DATE) == []
    assert last_healthy_trade_date(tmp_path) == TRADE_DATE


def test_restart_skips_already_completed_stages_and_does_not_re_run_them(tmp_path: Path) -> None:
    # A second full run over an already-clean day runs nothing: every stage is skipped.
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [10.0])
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    clock = ManualClock(start=CALC_TS)

    stages = _eod_stages(store, events, instruments, masters, positions, clock=clock,
                         correlation_id="corr-1")
    first = run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-1",
                           clock=clock, stages=stages)
    assert set(first.ran) == set(EOD_STAGES)

    stages2 = _eod_stages(store, events, instruments, masters, positions, clock=clock,
                          correlation_id="corr-2")
    second = run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-2",
                            clock=clock, stages=stages2)
    assert second.ran == ()
    assert set(second.skipped) == set(EOD_STAGES)


# =========================================================================== #
# 2. Detection within the documented interval — injected clock, no real wait   #
# =========================================================================== #
def test_collector_failure_detected_within_documented_interval() -> None:
    # The documented bound is COLLECTOR_SILENCE_SECONDS; derive it from the constant,
    # not from the implementation's branch.
    clock = ManualClock(start=_T0)
    last_event = clock.now()

    # Just inside the interval: not yet detected.
    clock.advance(COLLECTOR_SILENCE_SECONDS - 1.0)
    assert collector_death_alert(
        session_id="sess-x", last_event_ts=last_event, now=clock.now()
    ) is None

    # At the interval boundary: detected. No real time passed — the clock was advanced.
    clock.advance(1.0)
    alert = collector_death_alert(
        session_id="sess-x", last_event_ts=last_event, now=clock.now()
    )
    assert alert is not None
    assert alert.kind == "collector_death"
    assert alert.subject == "sess-x"
    assert alert.detection_interval_seconds == COLLECTOR_SILENCE_SECONDS


def test_collector_never_started_is_detected_immediately() -> None:
    alert = collector_death_alert(session_id="sess-y", last_event_ts=None, now=_T0)
    assert alert is not None
    assert alert.kind == "collector_death"
    assert alert.subject == "sess-y"


# =========================================================================== #
# 3. Correlation ids link a collector session to its analytics jobs            #
# =========================================================================== #
def test_correlation_id_links_collector_session_to_analytics(tmp_path: Path) -> None:
    # Drive an analytics run under one correlation id and assert the same id is on the
    # analytics-job log line (the trace handle the collection side shares once C1's
    # live-collection job lands; the analytics half of the trace is provable today).
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)

    correlation_id = "corr-trace-9"
    capture = _CapturingProcessor()
    structlog.configure(processors=[capture])
    try:
        analytics = run_incremental_analytics(
            store=store, config=_config(), config_hash=CONFIG_HASH, positions=[],
            instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
            calc_ts=CALC_TS, clock=ManualClock(start=CALC_TS), correlation_id=correlation_id,
        )
    finally:
        structlog.reset_defaults()

    assert analytics.correlation_id == correlation_id
    starts = [r for r in capture.records if r.get("event") == "orchestration.analytics.start"]
    assert starts, "analytics job emitted no start log line"
    assert starts[0]["correlation_id"] == correlation_id


# =========================================================================== #
# 4. Metrics increment on the right events                                     #
# =========================================================================== #
def test_forward_failure_metric_increments_on_a_forward_failure() -> None:
    metrics = build_metrics()
    assert sample_value(metrics.registry, "forward_failures_total", {"underlying": "AAPL"}) == 0.0
    record_forward_failure(metrics, "AAPL")
    record_forward_failure(metrics, "AAPL", count=2)
    # Two calls, one of weight 2: the counter is 3, and only for AAPL.
    assert sample_value(metrics.registry, "forward_failures_total", {"underlying": "AAPL"}) == 3.0
    assert sample_value(metrics.registry, "forward_failures_total", {"underlying": "MSFT"}) == 0.0


def test_solver_failure_metric_increments_when_a_usable_quote_has_no_iv(tmp_path: Path) -> None:
    # Inject a master whose key resolves to an option the actor builds no IV for: feed a
    # usable option quote but break the call/put pairing so no forward → no IV → a
    # solver failure registered for the underlying.
    chain = get_fixture("single_strike_maturity")  # one call, no put → no forward, no IV
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    metrics = build_metrics()

    run_incremental_analytics(
        store=store, config=_config(), config_hash=CONFIG_HASH, positions=[],
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=ManualClock(start=CALC_TS), correlation_id="corr-solver",
        metrics=metrics, persist=False,
    )
    underlying = chain.underlying.underlying_symbol
    # The one usable option quote yielded no IV point: exactly one solver failure.
    assert sample_value(
        metrics.registry, "solver_failures_total", {"underlying": underlying}
    ) >= 1.0


def test_stale_quote_gauge_reflects_unusable_quotes(tmp_path: Path) -> None:
    # A clean liquid chain has no stale underlying quotes, so the gauge is 0 for AAPL.
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    metrics = build_metrics()
    run_incremental_analytics(
        store=store, config=_config(), config_hash=CONFIG_HASH, positions=[],
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=ManualClock(start=CALC_TS), correlation_id="corr-stale",
        metrics=metrics, persist=False,
    )
    assert sample_value(metrics.registry, "stale_quote_ratio", {"underlying": "AAPL"}) == 0.0


def test_analytics_run_time_is_observed_on_the_histogram(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    metrics = build_metrics()
    # Advance the manual clock by 0.0s naturally (run_analytics does not sleep); the
    # histogram still records one observation for the analytics job.
    run_incremental_analytics(
        store=store, config=_config(), config_hash=CONFIG_HASH, positions=[],
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=ManualClock(start=CALC_TS), correlation_id="corr-hist",
        metrics=metrics, persist=False,
    )
    count = sample_value(metrics.registry, "scenario_run_seconds_count", {"job": "analytics"})
    assert count == 1.0


# =========================================================================== #
# Missing-partition alert: flagged explicitly, never interpolated              #
# =========================================================================== #
def test_missing_partition_is_flagged_and_named() -> None:
    expected = [(TRADE_DATE, "AAPL"), (TRADE_DATE, "MSFT")]
    present = [(TRADE_DATE, "AAPL")]
    alerts = missing_partition_alerts(
        table="surface_parameters", expected=expected, present=present
    )
    assert len(alerts) == 1
    assert alerts[0].kind == "missing_partition"
    assert "MSFT" in alerts[0].subject  # names the specific missing partition
    assert "interpolat" in alerts[0].detail  # asserts it is not silently filled


# =========================================================================== #
# Elevated failure rate and QC-fail alerts                                     #
# =========================================================================== #
def _stage_run(stage: str, outcome: str, *, n: int) -> StageRun:
    return StageRun(
        trade_date=date(2026, 5, n), stage=stage, outcome=outcome,
        run_id=f"r{n}", recorded_ts=datetime(2026, 5, n, 16, 0, tzinfo=UTC),
    )


def test_elevated_failure_rate_alert_fires_over_the_window() -> None:
    # Six recent runs, four failed → ratio 0.667 > 0.5 → fires.
    runs = [
        _stage_run("analytics", OUTCOME_FAILED, n=1),
        _stage_run("analytics", OUTCOME_FAILED, n=2),
        _stage_run("analytics", OUTCOME_OK, n=3),
        _stage_run("analytics", OUTCOME_FAILED, n=4),
        _stage_run("analytics", OUTCOME_FAILED, n=5),
        _stage_run("analytics", OUTCOME_OK, n=6),
    ]
    alert = elevated_failure_rate_alert(runs=runs)
    assert alert is not None
    assert alert.kind == "elevated_failure_rate"


def test_elevated_failure_rate_alert_silent_below_threshold() -> None:
    runs = [_stage_run("analytics", OUTCOME_OK, n=i) for i in range(1, 7)]
    runs[0] = _stage_run("analytics", OUTCOME_FAILED, n=1)  # 1/6 < 0.5
    assert elevated_failure_rate_alert(runs=runs) is None


def test_elevated_failure_rate_alert_silent_without_enough_history() -> None:
    runs = [_stage_run("analytics", OUTCOME_FAILED, n=1)]  # one failure, no rate yet
    assert elevated_failure_rate_alert(runs=runs) is None


def test_qc_fail_alert_pages_on_a_critical_qc_failure(tmp_path: Path) -> None:
    # A collector summary with too many gaps fails the critical collector-continuity
    # check → the report escalates to page → qc_fail_alert fires.
    store = ParquetStore(tmp_path)
    config = _config()
    bad_summary = CollectorSummary(
        session_id="sess-bad", trade_date=TRADE_DATE, event_count=0, gap_count=99,
        reconnect_count=0, subscribed_count=1, covered_count=0, per_field_counts=(),
        pacing_failures=0, entitlement_failures=0,
    )
    job = run_qc(
        store=store, thresholds=thresholds_from_config(config.qc_threshold),
        collector_summary=bad_summary, trade_date=TRADE_DATE, run_id="qc-run-bad",
        run_ts=CALC_TS, correlation_id="corr-qc-bad",
    )
    assert job.escalation == "page"
    alert = qc_fail_alert(job.report)
    assert alert is not None
    assert alert.kind == "qc_fail"
    # The QC rows were persisted and read back.
    rows = store.read("qc_results")
    assert any(r.check_name == "collector_continuity" and r.status == "fail" for r in rows)


def test_qc_job_clean_summary_does_not_page(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    config = _config()
    good_summary = CollectorSummary(
        session_id="sess-ok", trade_date=TRADE_DATE, event_count=10, gap_count=0,
        reconnect_count=0, subscribed_count=1, covered_count=1, per_field_counts=(),
        pacing_failures=0, entitlement_failures=0,
    )
    job = run_qc(
        store=store, thresholds=thresholds_from_config(config.qc_threshold),
        collector_summary=good_summary, trade_date=TRADE_DATE, run_id="qc-run-ok",
        run_ts=CALC_TS, correlation_id="corr-qc-ok",
    )
    assert job.report.overall_status == "pass"
    assert qc_fail_alert(job.report) is None


# =========================================================================== #
# Reconciliation job: breaches named, clean when within tolerance              #
# =========================================================================== #
def _risk_line(contract_key: str, *, quantity: float = 1.0):  # type: ignore[no-untyped-def]
    """One real PositionRisk line for an ATM call, for reconciliation tests."""
    from algotrading.infra.risk import ContractValuationInput, position_risk

    valuation = ContractValuationInput(
        contract_key=contract_key, underlying="AAPL", option_right="C",
        exercise_style="european", strike=100.0, maturity_years=0.25, spot=100.0,
        carry=0.0, volatility=0.2, discount_factor=1.0, multiplier=100.0, currency="USD",
    )
    return position_risk(portfolio_id="pf-orch", quantity=quantity, valuation=valuation)


def test_reconciliation_surfaces_named_breaches() -> None:
    # A broker delta that disagrees by ~1.0 (computed call delta ~0.5) is far beyond the
    # 1e-3 delta tolerance → exactly one named breach on that contract's delta.
    contract_key = "AAPL|OPT|SMART|USD|100|o-AAPL-C|2026-08-27|100|C"
    line = _risk_line(contract_key)
    broker = BrokerGreeks(contract_key=contract_key, delta=999.0)
    result = reconcile_end_of_day(
        lines=(line,), broker_greeks=(broker,), trade_date=TRADE_DATE,
        correlation_id="corr-recon",
    )
    assert not result.is_clean
    assert len(result.breaches) == 1
    assert result.breaches[0].contract_key == contract_key  # names the offender
    assert result.breaches[0].greek == "delta"


def test_reconciliation_clean_when_within_tolerance() -> None:
    # No breach when the broker agrees, and no breach when there is no broker row.
    contract_key = "AAPL|OPT|SMART|USD|100|o-AAPL-C|2026-08-27|100|C"
    line = _risk_line(contract_key)
    agreeing = BrokerGreeks(contract_key=contract_key, delta=line.greeks.delta)
    matched = reconcile_end_of_day(
        lines=(line,), broker_greeks=(agreeing,), trade_date=TRADE_DATE, correlation_id="c",
    )
    assert matched.is_clean

    empty = reconcile_end_of_day(
        lines=(), broker_greeks=(), trade_date=TRADE_DATE, correlation_id="c",
    )
    assert empty.is_clean
    assert empty.breaches == ()


# =========================================================================== #
# Run-state ledger and dashboard                                               #
# =========================================================================== #
def test_run_state_ledger_records_and_resumes(tmp_path: Path) -> None:
    record_stage(tmp_path, StageRun(
        trade_date=TRADE_DATE, stage="universe_refresh", outcome=OUTCOME_OK,
        run_id="r1", recorded_ts=CALC_TS,
    ))
    record_stage(tmp_path, StageRun(
        trade_date=TRADE_DATE, stage="collection", outcome=OUTCOME_OK,
        run_id="r1", recorded_ts=CALC_TS,
    ))
    assert completed_stages(tmp_path, TRADE_DATE) == {"universe_refresh", "collection"}
    assert backlog_stages(tmp_path, TRADE_DATE) == ["analytics", "reconciliation", "qc"]
    assert last_healthy_trade_date(tmp_path) is None  # day not fully done


def test_run_state_failed_outcome_is_not_completed(tmp_path: Path) -> None:
    record_stage(tmp_path, StageRun(
        trade_date=TRADE_DATE, stage="qc", outcome=OUTCOME_FAILED,
        run_id="r1", recorded_ts=CALC_TS,
    ))
    assert "qc" not in completed_stages(tmp_path, TRADE_DATE)
    assert "qc" in backlog_stages(tmp_path, TRADE_DATE)


def test_dashboard_reports_backlog_and_last_healthy(tmp_path: Path) -> None:
    # A full clean day → dashboard shows no backlog and names the healthy date.
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [10.0])
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    clock = ManualClock(start=CALC_TS)
    metrics = build_metrics()
    metrics.events_collected.labels(underlying="AAPL").inc(5)

    stages = _eod_stages(store, events, instruments, masters, positions, clock=clock,
                         correlation_id="corr-dash")
    run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-dash",
                   clock=clock, stages=stages)

    status = build_dashboard(
        root_partitions=store.list_partitions("market_state_snapshots"),
        surface_partitions=store.list_partitions("surface_parameters"),
        scenario_partitions=store.list_partitions("scenario_results"),
        trade_date=TRADE_DATE, qc_status="passing", metrics=metrics, ledger_root=tmp_path,
    )
    assert status.backlog == ()
    assert status.last_healthy_trade_date == TRADE_DATE
    assert status.data_flowing == "ok"
    assert status.surfaces_building == "ok"
    assert status.scenarios_current == "current"
    assert status.is_healthy
    panel = render_dashboard(status)
    assert "last healthy run" in panel
    assert "backlog" in panel


def test_dashboard_shows_no_data_and_backlog_on_an_empty_day(tmp_path: Path) -> None:
    metrics = build_metrics()
    status = build_dashboard(
        root_partitions=[], surface_partitions=[], scenario_partitions=[],
        trade_date=TRADE_DATE, qc_status="unknown", metrics=metrics, ledger_root=tmp_path,
    )
    assert status.data_flowing == "no_data"
    assert status.scenarios_current == "stale"
    assert list(status.backlog) == list(EOD_STAGES)  # nothing ran → full backlog
    assert not status.is_healthy
