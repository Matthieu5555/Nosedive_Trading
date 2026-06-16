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
    coverage_breach_alerts,
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
from algotrading.infra.orchestration.eod_runner import (
    analytics_qc_results,
    persist_triage,
)
from algotrading.infra.orchestration.run_state import OUTCOME_FAILED, OUTCOME_OK
from algotrading.infra.qc import (
    CHECK_DELTA_BAND_COMPLETENESS,
    CHECK_SURFACE_FIT_ERROR,
    CHECK_TENOR_COVERAGE_FLOOR,
    thresholds_from_config,
)
from algotrading.infra.risk import BrokerGreeks
from algotrading.infra.storage import ParquetStore
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, ChainFixture, get_fixture

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
TRADE_DATE = AS_OF.date()
CONFIG_HASH = {"cfg": "cfg-hash-orch"}

_T0 = datetime(2026, 5, 29, 13, 30, tzinfo=UTC)


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

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def __call__(
        self, _logger: object, _name: str, event_dict: MutableMapping[str, object]
    ) -> MutableMapping[str, object]:
        self.records.append(dict(event_dict))
        raise structlog.DropEvent


class _FakePushAdapter:

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
        store=store, config=_config(), config_hashes=CONFIG_HASH, positions=[],
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=clock, correlation_id="corr-shared",
    )
    structlog.reset_defaults()

    assert collection.summary.event_count > 0
    assert not analytics.outputs.is_empty()
    corr_ids = {r.get("correlation_id") for r in processor.records if "correlation_id" in r}
    assert corr_ids == {"corr-shared"}
    jobs_logged = {r.get("job") for r in processor.records if "job" in r}
    assert {"collection", "analytics"} <= jobs_logged


def test_collected_events_metric_increments_per_underlying(tmp_path: Path) -> None:
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
    captured = [e for e in store.read("raw_market_events") if e.session_id == "sess-metric"]
    expected = len(captured)
    assert expected == len(ticks)
    assert (
        sample_value(metrics.registry, "events_collected_total", {"underlying": "AAPL"})
        == expected
    )


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
    config = _config()
    thresholds = thresholds_from_config(config.qc_threshold)

    def _universe_stage():  # type: ignore[no-untyped-def]
        return refresh_universe(
            store=store, config=config, masters=masters, trade_date=TRADE_DATE,
            correlation_id=correlation_id, persist=False,
        )

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
            store=store, config=config, config_hashes=CONFIG_HASH, positions=positions,
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

    exploding = _eod_stages(
        store, events, instruments, masters, positions, clock=clock,
        correlation_id="corr-kill", analytics_explodes=True,
    )
    with pytest.raises(RuntimeError, match="simulated kill"):
        run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-kill",
                       clock=clock, stages=exploding)

    assert backlog_stages(tmp_path, TRADE_DATE) == ["analytics", "reconciliation", "qc"]
    assert "universe_refresh" in completed_stages(tmp_path, TRADE_DATE)
    assert "collection" in completed_stages(tmp_path, TRADE_DATE)
    assert last_healthy_trade_date(tmp_path) is None

    healthy = _eod_stages(
        store, events, instruments, masters, positions, clock=clock,
        correlation_id="corr-restart",
    )
    result = run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-restart",
                            clock=clock, stages=healthy)
    assert set(result.ran) == set(EOD_STAGES)

    snapshots_after_restart = store.read("market_state_snapshots")
    iv_after_restart = store.read("iv_points")
    risk_after_restart = store.read("risk_aggregates")

    rerun = run_incremental_analytics(
        store=store, config=_config(), config_hashes=CONFIG_HASH, positions=positions,
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=clock, correlation_id="corr-rerun",
    )
    assert store.read("market_state_snapshots") == snapshots_after_restart
    assert store.read("iv_points") == iv_after_restart
    assert store.read("risk_aggregates") == risk_after_restart
    assert len(rerun.outputs.snapshots) == len(snapshots_after_restart)

    assert backlog_stages(tmp_path, TRADE_DATE) == []
    assert last_healthy_trade_date(tmp_path) == TRADE_DATE


def test_refire_reruns_every_stage_and_reconverges(tmp_path: Path) -> None:
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
    snapshots_before = store.read("market_state_snapshots")
    iv_before = store.read("iv_points")

    stages2 = _eod_stages(store, events, instruments, masters, positions, clock=clock,
                          correlation_id="corr-2")
    second = run_end_of_day(store, trade_date=TRADE_DATE, correlation_id="corr-2",
                            clock=clock, stages=stages2)
    assert set(second.ran) == set(EOD_STAGES)
    assert store.read("market_state_snapshots") == snapshots_before
    assert store.read("iv_points") == iv_before


def test_collector_failure_detected_within_documented_interval() -> None:
    clock = ManualClock(start=_T0)
    last_event = clock.now()

    clock.advance(COLLECTOR_SILENCE_SECONDS - 1.0)
    assert collector_death_alert(
        session_id="sess-x", last_event_ts=last_event, now=clock.now()
    ) is None

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


def test_correlation_id_links_collector_session_to_analytics(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)

    correlation_id = "corr-trace-9"
    capture = _CapturingProcessor()
    structlog.configure(processors=[capture])
    try:
        analytics = run_incremental_analytics(
            store=store, config=_config(), config_hashes=CONFIG_HASH, positions=[],
            instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
            calc_ts=CALC_TS, clock=ManualClock(start=CALC_TS), correlation_id=correlation_id,
        )
    finally:
        structlog.reset_defaults()

    assert analytics.correlation_id == correlation_id
    starts = [r for r in capture.records if r.get("event") == "orchestration.analytics.start"]
    assert starts, "analytics job emitted no start log line"
    assert starts[0]["correlation_id"] == correlation_id


def test_forward_failure_metric_increments_on_a_forward_failure() -> None:
    metrics = build_metrics()
    assert sample_value(metrics.registry, "forward_failures_total", {"underlying": "AAPL"}) == 0.0
    record_forward_failure(metrics, "AAPL")
    record_forward_failure(metrics, "AAPL", count=2)
    assert sample_value(metrics.registry, "forward_failures_total", {"underlying": "AAPL"}) == 3.0
    assert sample_value(metrics.registry, "forward_failures_total", {"underlying": "MSFT"}) == 0.0


def test_solver_failure_metric_increments_when_a_usable_quote_has_no_iv(tmp_path: Path) -> None:
    chain = get_fixture("single_strike_maturity")
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    metrics = build_metrics()

    run_incremental_analytics(
        store=store, config=_config(), config_hashes=CONFIG_HASH, positions=[],
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=ManualClock(start=CALC_TS), correlation_id="corr-solver",
        metrics=metrics, persist=False,
    )
    underlying = chain.underlying.underlying_symbol
    assert sample_value(
        metrics.registry, "solver_failures_total", {"underlying": underlying}
    ) >= 1.0


def test_stale_quote_gauge_reflects_unusable_quotes(tmp_path: Path) -> None:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    _seed_raw_layer(store, events)
    metrics = build_metrics()
    run_incremental_analytics(
        store=store, config=_config(), config_hashes=CONFIG_HASH, positions=[],
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
    run_incremental_analytics(
        store=store, config=_config(), config_hashes=CONFIG_HASH, positions=[],
        instruments=instruments, masters=masters, trade_date=TRADE_DATE, as_of=AS_OF,
        calc_ts=CALC_TS, clock=ManualClock(start=CALC_TS), correlation_id="corr-hist",
        metrics=metrics, persist=False,
    )
    count = sample_value(metrics.registry, "scenario_run_seconds_count", {"job": "analytics"})
    assert count == 1.0


def test_missing_partition_is_flagged_and_named() -> None:
    expected = [(TRADE_DATE, "AAPL"), (TRADE_DATE, "MSFT")]
    present = [(TRADE_DATE, "AAPL")]
    alerts = missing_partition_alerts(
        table="surface_parameters", expected=expected, present=present
    )
    assert len(alerts) == 1
    assert alerts[0].kind == "missing_partition"
    assert "MSFT" in alerts[0].subject
    assert "interpolat" in alerts[0].detail


def _stage_run(stage: str, outcome: str, *, n: int) -> StageRun:
    return StageRun(
        trade_date=date(2026, 5, n), stage=stage, outcome=outcome,
        run_id=f"r{n}", recorded_ts=datetime(2026, 5, n, 16, 0, tzinfo=UTC),
    )


def test_elevated_failure_rate_alert_fires_over_the_window() -> None:
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
    runs[0] = _stage_run("analytics", OUTCOME_FAILED, n=1)
    assert elevated_failure_rate_alert(runs=runs) is None


def test_elevated_failure_rate_alert_silent_without_enough_history() -> None:
    runs = [_stage_run("analytics", OUTCOME_FAILED, n=1)]
    assert elevated_failure_rate_alert(runs=runs) is None


def test_qc_fail_alert_pages_on_a_critical_qc_failure(tmp_path: Path) -> None:
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
    rows = store.read("qc_results")
    assert any(r.check_name == "collector_continuity" and r.qc_status == "fail" for r in rows)


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


def _slice_fit(*, underlying: str, maturity: float, rmse: float, n_points: int = 5):  # type: ignore[no-untyped-def]
    from algotrading.infra.surfaces import METHOD_SVI, SliceFit

    return SliceFit(
        underlying=underlying,
        maturity_years=maturity,
        expiry_date=date(2026, 9, 1),
        day_count="ACT/365",
        method=METHOD_SVI,
        svi=None,
        rmse=rmse,
        n_points=n_points,
        arb_free=True,
        bound_hits=(),
        butterfly_violations=(),
        nonparametric_ks=(),
        nonparametric_ws=(),
        raw_points=(),
    )


def test_run_qc_emits_a_result_for_every_wired_analytics_and_grid_check(tmp_path: Path) -> None:
    from algotrading.infra.actor import ActorOutputs, QcInputs

    config = _grid_config()
    thresholds = thresholds_from_config(config.qc_threshold)
    qc_inputs = QcInputs(
        slice_fits=(
            _slice_fit(underlying="SPX", maturity=0.05, rmse=1e-6),
            _slice_fit(underlying="SPX", maturity=0.25, rmse=1e-6),
        )
    )
    extra = analytics_qc_results(
        ActorOutputs(), qc_inputs, thresholds=thresholds, run_id="qc-wired", run_ts=CALC_TS
    )
    assert len(extra) == 2
    assert {r.check_name for r in extra} == {CHECK_SURFACE_FIT_ERROR}
    assert all(r.qc_status == "pass" for r in extra)

    full = _full_cells("SPX", "10d") + _full_cells("SPX", "1m") + _full_cells("SPX", "3m")
    job = run_qc(
        store=ParquetStore(tmp_path),
        thresholds=thresholds,
        collector_summary=None,
        trade_date=TRADE_DATE,
        run_id="qc-wired",
        run_ts=CALC_TS,
        correlation_id="corr-qc-wired",
        grid_points={"SPX": full},
        tenor_grid=_GRID_TENORS,
        extra_results=extra,
    )
    produced = {r.check_name for r in job.report.results}
    assert {
        CHECK_TENOR_COVERAGE_FLOOR,
        CHECK_DELTA_BAND_COMPLETENESS,
        CHECK_SURFACE_FIT_ERROR,
    } <= produced
    assert job.report.overall_status == "pass"


def test_surface_fit_check_fails_and_triage_persists_a_row(tmp_path: Path) -> None:
    from algotrading.infra.actor import ActorOutputs, QcInputs

    store = ParquetStore(tmp_path)
    config = _grid_config()
    thresholds = thresholds_from_config(config.qc_threshold)
    assert thresholds.fit_tolerance.max_surface_rmse < 0.5

    qc_inputs = QcInputs(
        slice_fits=(_slice_fit(underlying="SPX", maturity=0.25, rmse=0.5),)
    )
    extra = analytics_qc_results(
        ActorOutputs(), qc_inputs, thresholds=thresholds, run_id="qc-fail", run_ts=CALC_TS
    )
    assert [r.qc_status for r in extra] == ["fail"]

    job = run_qc(
        store=store,
        thresholds=thresholds,
        collector_summary=None,
        trade_date=TRADE_DATE,
        run_id="qc-fail",
        run_ts=CALC_TS,
        correlation_id="corr-qc-fail",
        extra_results=extra,
    )
    assert job.report.overall_status == "fail"

    records = persist_triage(store, job.report, correlation_id="corr-qc-fail")
    assert len(records) == 1
    row = records[0]
    assert row.source == "qc"
    assert row.name == CHECK_SURFACE_FIT_ERROR
    assert row.status == "fail"
    assert row.underlying == "SPX"

    persisted = store.read("triage_records")
    assert len(persisted) == 1
    assert persisted[0].name == CHECK_SURFACE_FIT_ERROR
    assert persisted[0].run_id == "qc-fail"
    from algotrading.infra.validation import escalation_level

    assert escalation_level(records) == "notice"


def test_live_analytics_qc_runs_the_full_wired_check_set_over_a_real_run(tmp_path: Path) -> None:
    from algotrading.infra.actor import run_analytics_with_qc
    from algotrading.infra.qc import (
        CHECK_CALENDAR_SANITY,
        CHECK_FORWARD_STABILITY,
        CHECK_GREEK_SANITY,
        CHECK_IV_SOLVER_CONVERGENCE,
        CHECK_OPTION_CHAIN_COVERAGE,
        CHECK_PARITY_RESIDUAL,
        CHECK_SCENARIO_COMPLETENESS,
        CHECK_UNDERLYING_QUOTE_HEALTH,
        deserialize_context,
    )

    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    held = _call_options(chain)[:1]
    positions = _positions(held, [1.0])
    config = _config()
    thresholds = thresholds_from_config(config.qc_threshold)

    run = run_analytics_with_qc(
        events,
        positions,
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=CONFIG_HASH,
        as_of=AS_OF,
        calc_ts=CALC_TS,
    )
    assert run.qc_inputs.forward_estimates and run.qc_inputs.forward_estimates[0].is_usable
    assert run.qc_inputs.slice_fits
    assert run.qc_inputs.iv_results and run.qc_inputs.iv_results[0][1]
    assert run.qc_inputs.risk_lines
    assert run.qc_inputs.scenario_grid
    full_iv = run.qc_inputs.iv_results[0][1]
    assert len(run.outputs.iv_points) <= len(full_iv)

    results = analytics_qc_results(
        run.outputs, run.qc_inputs, thresholds=thresholds, run_id="live", run_ts=CALC_TS
    )
    produced = {r.check_name for r in results}
    assert {
        CHECK_SURFACE_FIT_ERROR,
        CHECK_FORWARD_STABILITY,
        CHECK_PARITY_RESIDUAL,
        CHECK_IV_SOLVER_CONVERGENCE,
        CHECK_CALENDAR_SANITY,
        CHECK_UNDERLYING_QUOTE_HEALTH,
        CHECK_OPTION_CHAIN_COVERAGE,
        CHECK_GREEK_SANITY,
        CHECK_SCENARIO_COMPLETENESS,
    } == produced

    by_name = {r.check_name: r for r in results}
    for name in (
        CHECK_SURFACE_FIT_ERROR,
        CHECK_FORWARD_STABILITY,
        CHECK_PARITY_RESIDUAL,
        CHECK_IV_SOLVER_CONVERGENCE,
        CHECK_CALENDAR_SANITY,
        CHECK_UNDERLYING_QUOTE_HEALTH,
        CHECK_GREEK_SANITY,
        CHECK_SCENARIO_COMPLETENESS,
    ):
        assert by_name[name].qc_status == "pass", name
    scenario_ctx = deserialize_context(by_name[CHECK_SCENARIO_COMPLETENESS].context)
    assert scenario_ctx["missing_count"] == 0
    expected_cells = len(run.qc_inputs.scenario_grid) * len(run.qc_inputs.risk_lines)
    assert scenario_ctx["expected_count"] == expected_cells
    coverage = by_name[CHECK_OPTION_CHAIN_COVERAGE]
    assert coverage.qc_status == "fail"
    assert deserialize_context(coverage.context)["missing_contracts"]


def test_live_analytics_qc_surfaces_a_fail_on_a_dropped_scenario_cell(tmp_path: Path) -> None:
    import dataclasses

    from algotrading.infra.actor import run_analytics_with_qc
    from algotrading.infra.qc import CHECK_SCENARIO_COMPLETENESS, deserialize_context

    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    positions = _positions(_call_options(chain)[:1], [1.0])
    config = _config()
    thresholds = thresholds_from_config(config.qc_threshold)

    run = run_analytics_with_qc(
        events, positions, instruments=instruments, masters=masters, config=config,
        config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
    )
    assert run.outputs.scenarios

    dropped = dataclasses.replace(run.outputs, scenarios=run.outputs.scenarios[1:])
    results = analytics_qc_results(
        dropped, run.qc_inputs, thresholds=thresholds, run_id="live-fail", run_ts=CALC_TS
    )
    scenario = next(r for r in results if r.check_name == CHECK_SCENARIO_COMPLETENESS)
    assert scenario.qc_status == "fail"
    scenario_ctx = deserialize_context(scenario.context)
    assert scenario_ctx["missing_count"] == 1
    missing = scenario_ctx["missing_cells"]
    assert len(missing) == 1
    assert "scenario_id" in missing[0] and "contract_key" in missing[0]


_GRID_TENORS = ("10d", "1m", "3m")


def _grid_config() -> PlatformConfig:
    from algotrading.core.config import GridQcConfig

    base = _config()
    grid = GridQcConfig(
        version="grid-1",
        tenor_floors={"10d": 2, "1m": 2, "3m": 2},
        band_low_delta=-0.30,
        band_high_delta=0.30,
        max_delta_step=0.35,
    )
    return base.model_copy(
        update={"qc_threshold": base.qc_threshold.model_copy(update={"grid": grid})},
    )


class _GridCell:

    def __init__(self, underlying: str, tenor: str, delta: float) -> None:
        self.underlying = underlying
        self.tenor_label = tenor
        self.target_delta = delta
        self.delta = delta


def _full_cells(underlying: str, tenor: str) -> list[_GridCell]:
    return [_GridCell(underlying, tenor, d) for d in (-0.30, 0.0, 0.30)]


def test_coverage_breach_alert_fires_per_breaching_tenor(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    thresholds = thresholds_from_config(_grid_config().qc_threshold)
    # A genuine liquid-core collapse: 10d and 3m are liquid (span [10d, 3m]); 1m sits strictly
    # inside it with a single point — a partial-capture CRITICAL breach (ADR 0052), which pages
    # a coverage_breach alert. Edge illiquidity (a missing 2y/3y) would only WARN and not page.
    thin = (
        _full_cells("SPX", "10d") + _full_cells("SPX", "3m") + [_GridCell("SPX", "1m", -0.30)]
    )
    job = run_qc(
        store=store, thresholds=thresholds, collector_summary=None,
        trade_date=TRADE_DATE, run_id="qc-grid-breach", run_ts=CALC_TS,
        correlation_id="corr-grid-breach",
        grid_points={"SPX": thin}, tenor_grid=_GRID_TENORS,
    )
    alerts = coverage_breach_alerts(job.report)
    assert {a.kind for a in alerts} == {"coverage_breach"}
    subjects = {a.subject for a in alerts}
    assert subjects == {"SPX@1m"}

    full = {"SPX": _full_cells("SPX", "10d") + _full_cells("SPX", "1m") + _full_cells("SPX", "3m")}
    clean_job = run_qc(
        store=store, thresholds=thresholds, collector_summary=None,
        trade_date=TRADE_DATE, run_id="qc-grid-clean", run_ts=CALC_TS,
        correlation_id="corr-grid-clean", grid_points=full, tenor_grid=_GRID_TENORS,
    )
    assert coverage_breach_alerts(clean_job.report) == []


def test_coverage_breach_and_missing_partition_are_distinct(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    thresholds = thresholds_from_config(_grid_config().qc_threshold)
    thin = (
        _full_cells("SPX", "10d")
        + [_GridCell("SPX", "1m", -0.30)]
        + _full_cells("SPX", "3m")
    )
    job = run_qc(
        store=store, thresholds=thresholds, collector_summary=None,
        trade_date=TRADE_DATE, run_id="qc-grid-thin", run_ts=CALC_TS,
        correlation_id="corr-grid-thin", grid_points={"SPX": thin}, tenor_grid=_GRID_TENORS,
    )
    cov_alerts = coverage_breach_alerts(job.report)
    assert {a.subject for a in cov_alerts} == {"SPX@1m"}

    missing = missing_partition_alerts(
        table="projected_option_analytics",
        expected=[(TRADE_DATE, "SPX"), (TRADE_DATE, "RUT")],
        present=[(TRADE_DATE, "SPX")],
    )
    assert len(missing) == 1
    assert "RUT" in missing[0].subject
    assert "SPX" not in missing[0].subject
    assert all("RUT" not in a.subject for a in cov_alerts)


def _risk_line(contract_key: str, *, quantity: float = 1.0):  # type: ignore[no-untyped-def]
    from algotrading.infra.risk import ContractValuationInput, position_risk

    valuation = ContractValuationInput(
        contract_key=contract_key, underlying="AAPL", option_right="C",
        exercise_style="european", strike=100.0, maturity_years=0.25, spot=100.0,
        carry=0.0, volatility=0.2, discount_factor=1.0, multiplier=100.0, currency="USD",
    )
    return position_risk(portfolio_id="pf-orch", quantity=quantity, valuation=valuation)


def test_reconciliation_surfaces_named_breaches() -> None:
    contract_key = "AAPL|OPT|SMART|USD|100|o-AAPL-C|2026-08-27|100|C"
    line = _risk_line(contract_key)
    broker = BrokerGreeks(contract_key=contract_key, delta=999.0)
    result = reconcile_end_of_day(
        lines=(line,), broker_greeks=(broker,), trade_date=TRADE_DATE,
        correlation_id="corr-recon",
    )
    assert not result.is_clean
    assert len(result.breaches) == 1
    assert result.breaches[0].contract_key == contract_key
    assert result.breaches[0].greek == "delta"


def test_reconciliation_clean_when_within_tolerance() -> None:
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
    assert last_healthy_trade_date(tmp_path) is None


def test_run_state_failed_outcome_is_not_completed(tmp_path: Path) -> None:
    record_stage(tmp_path, StageRun(
        trade_date=TRADE_DATE, stage="qc", outcome=OUTCOME_FAILED,
        run_id="r1", recorded_ts=CALC_TS,
    ))
    assert "qc" not in completed_stages(tmp_path, TRADE_DATE)
    assert "qc" in backlog_stages(tmp_path, TRADE_DATE)


def test_dashboard_reports_backlog_and_last_healthy(tmp_path: Path) -> None:
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
    assert list(status.backlog) == list(EOD_STAGES)
    assert not status.is_healthy


def _record_one_stage_at_barrier(
    root: Path, stage: str, run_id: str, barrier: object
) -> None:
    barrier.wait()  # type: ignore[attr-defined]
    record_stage(
        root,
        StageRun(
            trade_date=TRADE_DATE,
            stage=stage,
            outcome=OUTCOME_OK,
            run_id=run_id,
            recorded_ts=CALC_TS,
        ),
    )


def test_concurrent_record_stage_keeps_every_record(tmp_path: Path) -> None:
    import multiprocessing as mp

    from algotrading.infra.orchestration.run_state import read_stage_runs

    ctx = mp.get_context("fork")
    expected_stages = set(EOD_STAGES)
    barrier = ctx.Barrier(len(EOD_STAGES))
    procs = [
        ctx.Process(
            target=_record_one_stage_at_barrier,
            args=(tmp_path, stage, f"r-{stage}", barrier),
        )
        for stage in EOD_STAGES
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0, f"worker exited with {proc.exitcode}"

    runs = read_stage_runs(tmp_path)
    assert len(runs) == len(EOD_STAGES)
    assert {run.stage for run in runs} == expected_stages
    assert {run.run_id for run in runs} == {f"r-{stage}" for stage in EOD_STAGES}
