from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.connectivity import Clock
from algotrading.infra.contracts import SURFACE_SIDE_COMBINED, ProjectedOptionAnalytics
from algotrading.infra.qc import ESCALATION_NOTICE, ESCALATION_PAGE
from algotrading.infra.storage import ParquetStore

from .alert_delivery import AlertSink, deliver_alerts, resolve_alert_sink
from .alerts import coverage_breach_alerts, degenerate_close_alert, qc_fail_alert
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
    index_symbol: str | None = None,
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

    # Scope-aware QC severity (ADR 0060): the strict CRITICAL gates are calibrated for the one
    # tradeable index. A basket run mixes the index with its illiquid single-name constituents into
    # one QcInputs, so each CRITICAL check decides is_index by its own underlying. When
    # index_symbol is None (every pre-existing caller) is_index is True for everyone, preserving the
    # old strict behaviour exactly. Only the CRITICAL gates take the flag; the WARNING-severity
    # checks (surface fit, forward, parity, iv-solver, chain coverage) are unaffected by scope.
    def _is_index(underlying: str) -> bool:
        return index_symbol is None or underlying == index_symbol

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
                is_index=_is_index(underlying),
            )
        )

    if qc_inputs.batch is not None:
        # Anchor-quote health spans the whole captured batch (the index anchor included), so it
        # stays index-strict regardless of scope: a dead index anchor must still page.
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
                is_index=_is_index(risk_line.valuation.underlying),
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
    alert_sink: AlertSink | None = None,
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
    sink = alert_sink if alert_sink is not None else resolve_alert_sink()
    trade_date = fired[0].as_of.date() if fired else clock.now().date()
    thresholds = thresholds_from_config(config.qc_threshold)
    qc_ts = clock.now()
    # A fire is "intraday" when it runs before the session has closed — the manual early-run path,
    # a human firing eod_run before the close to eyeball provisional data. The production systemd
    # timer fires at/after the close, so it is never intraday and its QC is untouched. Intraday
    # captures are legitimately thin (one-sided wings, sparse front-week), so the QC plane would
    # flag expected midday artifacts as failures; on an intraday fire we skip QC entirely rather
    # than raise noise on data that is not the real close. `any` is conservative: if even one fired
    # index has not closed, the run as a whole is provisional. Decided here, not inside a check —
    # the checks stay pure and clock-free (qc/README.md).
    intraday = any(qc_ts < fired_index.as_of for fired_index in fired)

    baskets: dict[str, tuple[FiredIndex, IndexBasket]] = {}
    for fired_index in fired:
        basket = basket_source(fired_index, trade_date)
        if basket is None:
            continue
        symbol = fired_index.entry.symbol
        # Overwrite-protection gate (T-restore-overwrite-last-wins C1.2). overwrite-last-wins
        # is gated on a NON-EMPTY capture: an empty / closed-market / last-only re-fire (zero
        # valid two-sided quotes) must never overwrite a slice already banked for this
        # (trade_date, underlying). The boundary is ZERO valid two-sided quote — a thin-but-real
        # basket (count > 0) is ADMITTED and flagged downstream (front clamp), never dropped
        # (flag-not-reject). A first faithful land (no prior banked) is always admitted so raw
        # stays Tier-1 faithful and the degenerate detector can page (ADR-0040 fail-loud).
        if basket.two_sided_count == 0 and store.read(
            _RAW_MARKET_EVENTS, trade_date=trade_date, underlying=symbol
        ):
            log.warning(
                "orchestration.eod_run.rejected_empty_overwrite",
                index=symbol,
                trade_date=trade_date.isoformat(),
                reason="re-fire carried zero valid two-sided quotes; banked slice retained "
                "(overwrite-last-wins requires a non-empty capture) — raw + derived untouched; "
                "run still pages degenerate via the QC seam if nothing else banked",
            )
            continue
        baskets[symbol] = (fired_index, basket)

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
        # Overwrite-last-wins: each admitted (trade_date, underlying) raw slot is REPLACED by
        # the latest fire's events. Stable session_id + event_id (C1.1) make an identical
        # re-fire a no-op in effect; a corrected re-fire wins — blueprint 01-arch:17 (a re-run
        # is byte-for-byte idempotent OR intentionally versioned; version= stays the deliberate-
        # replay hatch, never the routine). The admission gate above guarantees this delete can
        # never wipe a banked slice for an empty / closed-market re-fire.
        #
        # The slots to replace are every underlying PRESENT in the captured events, not just the
        # fired index symbols: since constituent capture was re-enabled (ADR 0059) the basket
        # merged under each fired index spans the index AND its constituent option chains, so each
        # fire writes raw rows for ~50 underlyings. Clearing only the index slot left the prior
        # fire's constituent rows in place, and a same-day re-fetch then died on a duplicate raw
        # primary key (append-only) before anything landed. Deleting exactly the underlyings we are
        # about to rewrite keeps the per-(trade_date, underlying) last-valid-wins semantics and
        # never touches a slot this fire did not re-capture.
        for underlying in {event.underlying for event in events}:
            store.delete_partition(_RAW_MARKET_EVENTS, trade_date, underlying)
        if events:
            store.write(_RAW_MARKET_EVENTS, events)
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
            raw_events_landed=len(events),
            reason="captured close events overwrite-landed to raw_market_events "
            "(last-valid-wins, 1C)",
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
                    index_symbol=fired_index.entry.symbol,
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
        # QC ALWAYS runs and ALWAYS records its verdict — intraday is not a free pass. run_qc judges
        # the captured data against the thresholds and persist_triage records the named offenders,
        # so a human reads whether a provisional midday capture is genuinely sound or genuinely
        # broken. Intraday changes only the *consequence* of a fail, never whether we look (below).
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
            # The basket keys are exactly the fired index symbols; every other captured underlying
            # is a constituent and gets notice-level grid QC (ADR 0060).
            index_symbols=frozenset(baskets),
        )
        triage = persist_triage(store, job.report, correlation_id=correlation_id)
        log.info(
            "orchestration.eod_run.triage_persisted",
            triage_row_count=len(triage),
            escalation=job.escalation,
        )

        if intraday:
            # A fire before the session close captures provisional data: one-sided wings and a
            # sparse front-week are EXPECTED midday, not a failed close. The QC verdict is recorded
            # above for inspection, but intraday it is INFORMATIONAL — we cap the escalation below
            # PAGE so the runner exits 0 and the close pager stays silent, and we fire none of the
            # close-incident alerts (qc_fail / coverage / degenerate). The verdict stays honest: a
            # genuinely-degenerate intraday capture still records FAIL in the report/triage; it
            # simply does not page. The production timer fires at/after the close, so a production
            # run is never intraday and never takes this branch (its close QC is untouched below).
            capped = ESCALATION_NOTICE if job.escalation == ESCALATION_PAGE else job.escalation
            log.info(
                "orchestration.eod_run.qc_intraday_informational",
                overall_status=job.report.overall_status,
                fail_count=job.report.fail_count,
                warn_count=job.report.warn_count,
                escalation=capped,
                escalation_before_cap=job.escalation,
                reason="fired before close; QC verdict recorded but not paged on provisional data",
            )
            return replace(job, escalation=capped)

        # A degenerate close — no basket captured at all, or baskets captured but zero usable
        # combined-surface grid cells (market-closed / last-only below the two-sided floor) — is
        # the silent-green gap: every stage exits OUTCOME_OK, run_qc has nothing to fail on, and
        # the run reads as "done" with no data banked. Detect it, alert it through the same C4
        # delivery seam, and force the escalation to PAGE so eod_runner returns non-zero (engaging
        # systemd OnFailure=) instead of exit 0.
        degenerate = degenerate_close_alert(
            correlation_id=correlation_id,
            captured_indices=sorted(baskets),
            analytics_grid_cells=sum(len(cells) for cells in grid_cells.values()),
        )
        results = deliver_alerts(
            sink,
            (qc_fail_alert(job.report), *coverage_breach_alerts(job.report), degenerate),
            {"correlation_id": correlation_id, "trade_date": trade_date.isoformat()},
        )
        for result in results:
            log.info(
                "orchestration.eod_run.alert_delivery",
                alert_kind=result.alert_kind,
                channel=result.channel,
                delivered=result.delivered,
                degraded=result.degraded,
                detail=result.detail,
            )
        if degenerate is not None and job.escalation != ESCALATION_PAGE:
            log.error(
                "orchestration.eod_run.degenerate_close_escalated",
                subject=degenerate.subject,
                detail=degenerate.detail,
                prior_escalation=job.escalation,
            )
            return replace(job, escalation=ESCALATION_PAGE)
        return job

    return EodStages(
        universe_refresh=_universe_refresh,
        collection=_collection,
        analytics=_analytics,
        reconciliation=_reconciliation,
        qc=_qc,
    )
