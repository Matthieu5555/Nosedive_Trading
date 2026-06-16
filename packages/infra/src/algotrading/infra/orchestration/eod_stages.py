from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.connectivity import Clock
from algotrading.infra.contracts import SURFACE_SIDE_COMBINED, ProjectedOptionAnalytics
from algotrading.infra.storage import ParquetStore

from .eod_planning import EOD_JOB_NAME, FiredIndex
from .jobs import (
    AnalyticsResult,
    CollectionResult,
    ReconciliationResult,
    UniverseRefreshResult,
    reconcile_end_of_day,
    refresh_universe,
)
from .pipeline import EodStages
from .qc_job import QcJobResult, run_qc

if TYPE_CHECKING:
    from algotrading.infra.actor import ActorOutputs, IndexBasket, QcInputs
    from algotrading.infra.contracts import QcResult, TriageRecord
    from algotrading.infra.qc import QcReport, QcThresholds
    from algotrading.infra.risk import BrokerGreeks

_LOGGER = structlog.get_logger("orchestration.eod_run")

_RAW_MARKET_EVENTS = "raw_market_events"


BasketSource = Callable[[FiredIndex, date], "IndexBasket | None"]


def _empty_basket_source(fired: FiredIndex, trade_date: date) -> IndexBasket | None:
    _LOGGER.info(
        "orchestration.eod_run.no_basket_source",
        index=fired.entry.symbol,
        trade_date=trade_date.isoformat(),
        reason="1C broker->raw-event collection seam not yet closed; capturing no basket",
    )
    return None


_TRIAGE_RECORDS_TABLE = "triage_records"


def analytics_qc_results(
    outputs: ActorOutputs,
    qc_inputs: QcInputs,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
    broker_greeks: Mapping[str, BrokerGreeks] | None = None,
) -> tuple[QcResult, ...]:
    from algotrading.infra.qc import (
        check_calendar_sanity,
        check_forward_stability,
        check_greek_sanity,
        check_iv_solver_convergence,
        check_option_chain_coverage,
        check_parity_residual,
        check_scenario_completeness,
        check_surface_fit_error,
        check_underlying_quote_health,
    )

    brokers = dict(broker_greeks) if broker_greeks is not None else {}
    results: list[QcResult] = []

    for fit in qc_inputs.slice_fits:
        results.append(
            check_surface_fit_error(fit, thresholds=thresholds, run_id=run_id, run_ts=run_ts)
        )

    for estimate in qc_inputs.forward_estimates:
        if not estimate.is_usable:
            continue
        results.append(
            check_forward_stability(
                estimate, thresholds=thresholds, run_id=run_id, run_ts=run_ts
            )
        )

    for underlying, maturity_years, line in qc_inputs.parity_lines:
        results.append(
            check_parity_residual(
                line, underlying, maturity_years,
                thresholds=thresholds, run_id=run_id, run_ts=run_ts,
            )
        )

    for underlying, solver_results in qc_inputs.iv_results:
        results.append(
            check_iv_solver_convergence(
                solver_results, underlying,
                thresholds=thresholds, run_id=run_id, run_ts=run_ts,
            )
        )

    for underlying, violations in qc_inputs.calendar_violations:
        results.append(
            check_calendar_sanity(
                violations, underlying,
                thresholds=thresholds, run_id=run_id, run_ts=run_ts,
            )
        )

    if qc_inputs.batch is not None:
        results.append(
            check_underlying_quote_health(
                qc_inputs.batch, qc_inputs.underlying_keys,
                thresholds=thresholds, run_id=run_id, run_ts=run_ts,
            )
        )
        for underlying, expected_keys in qc_inputs.expected_chain_keys:
            results.append(
                check_option_chain_coverage(
                    qc_inputs.batch, underlying, expected_keys,
                    thresholds=thresholds, run_id=run_id, run_ts=run_ts,
                )
            )

    for risk_line in qc_inputs.risk_lines:
        results.append(
            check_greek_sanity(
                risk_line,
                broker=brokers.get(risk_line.contract_key),
                thresholds=thresholds, run_id=run_id, run_ts=run_ts,
            )
        )

    if qc_inputs.risk_lines:
        contracts = tuple(line.contract_key for line in qc_inputs.risk_lines)
        expected_cells = [
            (scenario.scenario_id, contract_key)
            for scenario in qc_inputs.scenario_grid
            for contract_key in contracts
        ]
        produced_cells = [(row.scenario_id, row.contract_key) for row in outputs.scenarios]
        results.append(
            check_scenario_completeness(
                produced_cells,
                expected_cells,
                qc_inputs.portfolio_id,
                thresholds=thresholds, run_id=run_id, run_ts=run_ts,
            )
        )

    return tuple(results)


def persist_triage(
    store: ParquetStore,
    report: QcReport,
    *,
    correlation_id: str,
) -> tuple[TriageRecord, ...]:
    from algotrading.infra.validation import build_triage

    records = build_triage(qc_report=report)
    if records:
        store.write(_TRIAGE_RECORDS_TABLE, list(records))
    return records


StagesBuilder = Callable[
    [ParquetStore, PlatformConfig, "Mapping[str, str]", Clock, str, Sequence[FiredIndex]],
    EodStages,
]


def default_stages_builder(
    store: ParquetStore,
    config: PlatformConfig,
    hashes: Mapping[str, str],
    clock: Clock,
    correlation_id: str,
    fired: Sequence[FiredIndex],
    *,
    basket_source: BasketSource = _empty_basket_source,
) -> EodStages:
    from algotrading.infra.actor import (
        ActorOutputs,
        persist_outputs,
        run_analytics_with_qc,
    )
    from algotrading.infra.actor.basket import DEFAULT_PROVIDER
    from algotrading.infra.collectors import summarize_session
    from algotrading.infra.qc import thresholds_from_config
    from algotrading.infra.signals import persist_signal_set, signal_config_for

    log = _LOGGER.bind(correlation_id=correlation_id, job=EOD_JOB_NAME)
    trade_date = fired[0].as_of.date() if fired else clock.now().date()
    thresholds = thresholds_from_config(config.qc_threshold)
    qc_ts = clock.now()

    baskets: dict[str, tuple[FiredIndex, IndexBasket]] = {}
    for fired_index in fired:
        basket = basket_source(fired_index, trade_date)
        if basket is not None:
            baskets[fired_index.entry.symbol] = (fired_index, basket)

    def _universe_refresh() -> UniverseRefreshResult:
        masters = [
            master
            for _fired, basket in baskets.values()
            for master in basket.masters
        ]
        return refresh_universe(
            store=store,
            config=config,
            masters=masters,
            trade_date=trade_date,
            correlation_id=correlation_id,
        )

    def _collection() -> CollectionResult:
        events = [event for _fired, basket in baskets.values() for event in basket.events]
        subscribed = sorted(
            {key.canonical() for _fired, basket in baskets.values() for key in basket.instruments}
        )
        existing_ids = {
            event.event_id for event in store.read(_RAW_MARKET_EVENTS, trade_date=trade_date)
        }
        fresh = [event for event in events if event.event_id not in existing_ids]
        if fresh:
            store.write(_RAW_MARKET_EVENTS, fresh)
        summary = summarize_session(
            events,
            session_id=correlation_id,
            trade_date=trade_date,
            subscribed_keys=subscribed,
            reconnect_count=0,
        )
        log.info(
            "orchestration.eod_run.collection_landed",
            captured_indices=sorted(baskets),
            raw_events_landed=len(fresh),
            raw_events_total=len(events),
            reason="captured close events landed to raw_market_events before analytics (1C)",
        )
        return CollectionResult(
            correlation_id=correlation_id, session_id=correlation_id, summary=summary
        )

    grid_cells: dict[str, list[ProjectedOptionAnalytics]] = {}
    analytics_results: list[QcResult] = []

    def _analytics() -> AnalyticsResult:
        started = clock.now()
        outputs = ActorOutputs()
        for _symbol, (fired_index, basket) in sorted(baskets.items()):
            run = run_analytics_with_qc(
                basket.events,
                basket.positions,
                instruments=basket.instruments,
                masters=basket.masters,
                config=config,
                config_hashes=dict(hashes),
                as_of=fired_index.as_of,
                calc_ts=fired_index.as_of,
                session_open=False,
                provider=DEFAULT_PROVIDER,
            )
            outputs = run.outputs
            persist_outputs(store, outputs)
            for cell in outputs.projected_analytics:
                if cell.surface_side != SURFACE_SIDE_COMBINED:
                    continue
                grid_cells.setdefault(cell.underlying, []).append(cell)
            analytics_results.extend(
                analytics_qc_results(
                    outputs, run.qc_inputs,
                    thresholds=thresholds, run_id=correlation_id, run_ts=qc_ts,
                )
            )

        signal_rows_written = 0
        for _symbol, (fired_index, _basket) in sorted(baskets.items()):
            persisted_signals = persist_signal_set(
                store,
                signal_config_for(
                    config.universe.signals,
                    index=fired_index.entry.symbol,
                    provider=DEFAULT_PROVIDER,
                ),
                fired_index.as_of.date(),
                calc_ts=fired_index.as_of,
                config_hashes=dict(hashes),
            )
            signal_rows_written += len(persisted_signals)
        if baskets:
            log.info(
                "orchestration.eod_run.signals_persisted",
                captured_indices=sorted(baskets),
                signal_row_count=signal_rows_written,
                reason="daily as-of strategy-entry signals derived from the persisted grid (R3)",
            )

        return AnalyticsResult(
            correlation_id=correlation_id,
            trade_date=trade_date,
            outputs=outputs,
            run_seconds=(clock.now() - started).total_seconds(),
        )

    def _reconciliation() -> ReconciliationResult:
        return reconcile_end_of_day(
            lines=[], broker_greeks=[], trade_date=trade_date, correlation_id=correlation_id
        )

    def _qc() -> QcJobResult:
        job = run_qc(
            store=store,
            thresholds=thresholds,
            collector_summary=None,
            trade_date=trade_date,
            run_id=correlation_id,
            run_ts=qc_ts,
            correlation_id=correlation_id,
            grid_points=dict(grid_cells) or None,
            tenor_grid=config.universe.tenor_grid,
            extra_results=tuple(analytics_results),
        )
        triage = persist_triage(store, job.report, correlation_id=correlation_id)
        log.info(
            "orchestration.eod_run.triage_persisted",
            triage_row_count=len(triage),
            escalation=job.escalation,
        )
        return job

    return EodStages(
        universe_refresh=_universe_refresh,
        collection=_collection,
        analytics=_analytics,
        reconciliation=_reconciliation,
        qc=_qc,
    )
