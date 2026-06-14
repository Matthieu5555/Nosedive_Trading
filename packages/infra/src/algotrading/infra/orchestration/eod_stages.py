"""The EOD stage wiring — the live default :class:`EodStages` builder and its QC stage helpers.

This holds the production stage wiring (the close-capture/project_grid/persist path plus the
EOD jobs), the analytics-plane QC row builder, and the triage persist. The runner shell injects
:func:`default_stages_builder` as the default :data:`StagesBuilder`; a test swaps a fake.
"""

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

# The append-only raw layer the collection stage lands captured close events to, before analytics
# (blueprint Part III Step 3/4: raw is the evidentiary record, persisted and replayable from disk
# without reaching back to the broker). Same canonical table the replay/collector path reads/writes.
_RAW_MARKET_EVENTS = "raw_market_events"


# The 1C basket source: resolve the close-session basket (instruments + close events + masters)
# to capture for one fired index on its trade date. This is the *one* seam still gated on 1C's
# broker->raw-event bridge: until that lands there is no live source of close events, so the
# default source (:func:`_empty_basket_source`) returns ``None`` (a clearly-labeled, narrow gap
# — "no basket captured for this index yet"), NOT a raise. Everything downstream of a basket —
# the close-capture actor, the project_grid regrid, the persist — is fully wired and runs the
# moment a real basket source is injected here. Injected so 1C swaps in `collect_live`-backed
# baskets without touching the runner, the manifest freeze, or the timer.
BasketSource = Callable[[FiredIndex, date], "IndexBasket | None"]


def _empty_basket_source(fired: FiredIndex, trade_date: date) -> IndexBasket | None:
    """The default 1C-gap basket source: no live broker bridge yet, so no basket is captured.

    Returns ``None`` for every index — the narrow, labeled gap that replaces the old
    blanket raise. A fire then runs every stage cleanly over an empty captured set (a clean
    no-capture day, exit 0) rather than dying with exit 1 before any stage runs. The instant
    1C lands a real basket source, the wired close-capture/project_grid/persist path produces
    and stores the grid with no other change here.
    """
    _LOGGER.info(
        "orchestration.eod_run.no_basket_source",
        index=fired.entry.symbol,
        trade_date=trade_date.isoformat(),
        reason="1C broker->raw-event collection seam not yet closed; capturing no basket",
    )
    return None


# The triage table the unified validation/QC plane folds into (one persisted shape, ADR 0010).
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
    """Build the analytics-plane :class:`QcResult` rows from one run's outputs + QC intermediates.

    The live analytics stage now returns, beside the persisted :class:`ActorOutputs`, an
    :class:`actor.QcInputs` carrying the rich domain objects the named checks consume — the
    per-maturity :class:`forwards.ForwardEstimate`, the *full* :class:`iv.IvResult` set
    (including the non-converged solves the persisted ``iv_points`` drop), the rich
    :class:`surfaces.SliceFit`, the netted :class:`risk.PositionRisk` lines, the
    :class:`snapshots.SnapshotBatch`, the precomputed per-underlying calendar violations, and the
    scenario grid the reprice ran over. This runs every check whose input that bundle genuinely
    carries, producing one specific ``QcResult`` per target:

    * **always live** (the bundle always carries their input on a non-empty run):
      :func:`check_surface_fit_error` (per slice), :func:`check_forward_stability` and
      :func:`check_parity_residual` (per usable forward), :func:`check_iv_solver_convergence`
      (per underlying), :func:`check_calendar_sanity` (per underlying),
      :func:`check_underlying_quote_health` (one, over the batch), and
      :func:`check_option_chain_coverage` (per underlying).
    * **live when positions are present**: :func:`check_greek_sanity` (per netted line) and
      :func:`check_scenario_completeness` (per portfolio over the actual grid × contracts). A
      no-position run carries no lines, so they emit nothing — correct, not a fabricated pass.
    * **conditional**: :func:`check_greek_sanity`'s broker-reconciliation arm runs only for a
      line whose ``contract_key`` has a ``broker_greeks`` row; the default live path has no
      broker-greek feed, so the sign/finiteness arm runs and reconciliation is cleanly skipped.

    Returned as a tuple so the live QC stage threads them into :func:`run_qc`'s ``extra_results``
    — the escape hatch that rolls already-built rows into the one report. No input here is
    fabricated to make a check runnable: a degenerate run simply emits fewer rows.
    """
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
        # Produced cells are read back from the *persisted* scenario rows, so a reprice that
        # silently dropped a (scenario, contract) cell fails this check rather than passing on a
        # re-derived expectation. The cartesian above is the actor's own grid × its netted lines.
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
    """Fold a QC report through :func:`validation.build_triage` and persist the rows.

    The unified triage plane (ADR 0010) collapses both quality planes into one
    ``triage_records`` table; this is the orchestration-side write the validation layer
    deliberately leaves to its caller (it never does I/O). Only the non-passing rows become
    triage records, so a clean run writes nothing. The validation half is absent here (the
    live path runs no anomaly/validation plane yet), so this folds the QC report alone. Returns
    the persisted records so the caller can log/escalate on them; persisting is idempotent on the
    record key, mirroring the QC-row write.
    """
    from algotrading.infra.validation import build_triage

    records = build_triage(qc_report=report)
    if records:
        store.write(_TRIAGE_RECORDS_TABLE, list(records))
    return records


# The default-wiring builder: given the resolved store/config/clock/trace and the fired indices
# (each with its own close instant), return the five-stage :class:`EodStages`. Injected so a
# test supplies a fake wiring (including one whose stage raises) with no broker. The production
# default (:func:`default_stages_builder`) wires the close-capture collection seam (1C) and the
# existing job functions; until 1C lands the collection stage is a replay/fixture stage.
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
    """The live default wiring — the close-capture/project_grid/persist path plus the EOD jobs.

    Builds the five real :class:`EodStages` over the live collaborators already assembled in
    :func:`build_default_deps` (the :class:`ParquetStore`, the resolved config + hashes, the
    injected clock, the fired index baskets), so a real systemd fire flows
    capture → analytics(project_grid) → persist → reconciliation → QC instead of raising.

    The one seam still gated on 1C is the *source of captured baskets*: ``basket_source``
    resolves each fired index's close-session basket, and its default
    (:func:`_empty_basket_source`) returns ``None`` until the broker->raw-event bridge lands —
    a narrow, clearly-labeled gap, not a blanket raise. With no basket the analytics stage
    persists nothing (a clean no-capture day, exit 0); with a basket injected it runs the full
    close-capture actor, regrids onto the pinned tenor × delta-band grid via
    :func:`surfaces.project_grid`, and persists the :class:`ProjectedOptionAnalytics` rows.

    Each fired index is captured at *its own* ``FiredIndex.as_of`` (the resolver's session
    close), so a multi-exchange fire prices each index at its own close instant. The grid's
    provider-partitioned cells are stamped with :data:`DEFAULT_PROVIDER`.
    """
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

    # Resolve each fired index's basket once (the 1C seam), shared across the stages.
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
        # Land the captured baskets' raw close events to the append-only raw layer BEFORE analytics
        # (blueprint Part III Step 3/4: the raw layer is the evidentiary record, persisted and
        # replayable from disk without reaching back to the broker). Without this, an analytics
        # failure on the close basket loses the marks irrecoverably — the live snapshot is a current
        # quote that cannot be re-fetched for a past close (no look-ahead). Once landed, the day is
        # reconstructable via the replay collector. raw_market_events is content-addressed on
        # event_id, so a re-fire is filtered to a clean no-op rather than an append-only collision
        # (the first landed close is immutable). With no captured basket this writes nothing — the
        # clean no-capture day, exit 0 — exactly as before.
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
    # The analytics-plane QcResults built off each index's run (its persisted ActorOutputs plus
    # the in-memory QcInputs intermediates), accumulated across baskets exactly as ``grid_cells``
    # is, so the live QC stage runs the full wired analytics/risk check set over every captured
    # index — not only the grid checks.
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
                # Grid coverage / Δ-band completeness QC is about the combined surface — the
                # reference grid (ADR 0048). The per-side put/call rows are an additive
                # diagnostic; counting them would triple the coverage and skew the band span.
                if cell.surface_side != SURFACE_SIDE_COMBINED:
                    continue
                grid_cells.setdefault(cell.underlying, []).append(cell)
            analytics_results.extend(
                analytics_qc_results(
                    outputs, run.qc_inputs,
                    thresholds=thresholds, run_id=correlation_id, run_ts=qc_ts,
                )
            )

        # Strategy-entry signal layer (R3 / §3): with every captured index's combined-surface
        # grid now persisted, derive and persist the daily as-of signal set the §3 book triggers
        # on (S1 ρ̄ + IV-rank/RV−IV/term-slope). This mirrors how the projection grid is persisted
        # in this same stage — read the as-of inputs at the analytics choke, call
        # persist_signal_set. Each index computes at its OWN session close (``fired_index.as_of``,
        # the grid's calc_ts), so every read is gated to that instant (look-ahead clean) and the
        # signal stamp is replay-stable. A signal the day cannot answer (no constituent surfaces
        # for ρ̄, a flat IV window, too few bars) is omitted, never fabricated; an index-only
        # capture still yields the index's own term slope. Without this the signal partition was
        # written by nothing and S1's ρ̄ entry read an empty store every day.
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
        # No broker Greek feed in the no-1C path → nothing to reconcile against (clean). The
        # join is over the captured baskets' positions when a broker feed is injected later.
        return reconcile_end_of_day(
            lines=[], broker_greeks=[], trade_date=trade_date, correlation_id=correlation_id
        )

    def _qc() -> QcJobResult:
        # Run grid checks (grid_points), the analytics checks wired off ActorOutputs
        # (extra_results), and any collector-continuity check together so they roll into one
        # report/escalation. Then fold the report through the unified triage plane and persist
        # the non-passing rows to the triage_records table (ADR 0010).
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
