"""The QC job — run the validation checks over a day's outputs, write the rows, alert.

The QC plane (``infra.qc``) is a pure check library: it does not read a clock, persist
anything, or schedule itself. This job is the thin operable wrapper around it. It is
handed the day's results (a collector summary, snapshot batch, forwards, IV results,
slice fits, risk lines) already assembled, runs the checks it has inputs for with an
injected ``run_id``/``run_ts``, rolls them into a report, writes the ``QcResult`` rows
to A's ``qc_results`` table, and returns the report plus its escalation level so the
alerting layer has a single signal.

Writing the rows is idempotent on the QC result's primary key, so re-running the QC
job for the same ``run_id`` replaces the same partition rather than duplicating it —
the restart guarantee the pipeline leans on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

import structlog
from algotrading.infra.contracts import QcResult
from algotrading.infra.qc import (
    STATUS_PASS,
    CollectorContinuityInput,
    GridPointInput,
    QcReport,
    QcThresholds,
    build_report,
    check_collector_continuity,
    check_delta_band_completeness,
    check_tenor_coverage_floor,
    escalation_level,
    result_headline,
)
from algotrading.infra.storage import ParquetStore

_LOGGER = structlog.get_logger("orchestration")

_QC_RESULTS_TABLE = "qc_results"


@dataclass(frozen=True, slots=True)
class QcJobResult:
    """The outcome of one QC job: the report, the escalation level, the persisted rows."""

    correlation_id: str
    trade_date: date
    report: QcReport
    escalation: str
    results: tuple[QcResult, ...] = field(default_factory=tuple)

    @property
    def overall_status(self) -> str:
        """The worst single check status this run produced (pass/warn/fail)."""
        return self.report.overall_status


def run_qc(
    *,
    store: ParquetStore,
    thresholds: QcThresholds,
    collector_summary: CollectorContinuityInput | None,
    trade_date: date,
    run_id: str,
    run_ts: datetime,
    correlation_id: str,
    grid_points: Mapping[str, Sequence[GridPointInput]] | None = None,
    tenor_grid: Sequence[str] = (),
    extra_results: Sequence[QcResult] = (),
    persist: bool = True,
) -> QcJobResult:
    """Run the available QC checks, roll a report, persist the rows, report escalation.

    ``collector_summary`` drives the collector-continuity check (the one this job
    always has an input for in the EOD sequence); callers with more day-end objects
    (forwards, IV results, slice fits, risk lines) pass their already-built
    ``QcResult`` rows via ``extra_results`` so this job stays the single place that
    rolls the report, writes the rows, and computes escalation. ``grid_points`` (an
    ``underlying -> projected grid cells`` map) drives the two grid-aware checks (WS 1H):
    when supplied, the per-tenor coverage-floor and Δ-band-completeness checks run per
    underlying against the pinned ``tenor_grid`` (config, P0.1) and roll into the same
    report — they are mirrored on the ``collector_summary`` injection, not a second path.

    ``run_id`` and ``run_ts`` are injected and stamped on every row, so the QC output
    reproduces in replay. Persisting is idempotent on the result key (replace-semantics).
    Returns the report and the one escalation signal an alert layer thresholds on.

    The collector input is typed as the structural :class:`qc.CollectorContinuityInput`
    Protocol rather than a concrete summary class, so any collector summary that carries
    the continuity fields satisfies it with no adapter — the seam C2 froze so the QC
    plane never imports the collector plane.
    """
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="qc",
        trade_date=trade_date.isoformat(),
        run_id=run_id,
    )
    results: list[QcResult] = list(extra_results)
    if collector_summary is not None:
        results.append(
            check_collector_continuity(
                collector_summary, thresholds=thresholds, run_id=run_id, run_ts=run_ts
            )
        )

    # Grid-aware QC (WS 1H): when the job is handed the day's projected grid points, run the
    # per-tenor coverage-floor and Δ-band-completeness checks per underlying. They produce
    # ordinary QcResults that roll into the same report/escalation and persist with the rest —
    # no second path. The pinned ``tenor_grid`` is config (P0.1), passed in, never read from
    # the data under test.
    if grid_points is not None:
        for underlying in sorted(grid_points):
            points = grid_points[underlying]
            results.append(
                check_tenor_coverage_floor(
                    points, underlying, tenor_grid,
                    thresholds=thresholds, run_id=run_id, run_ts=run_ts,
                )
            )
            results.append(
                check_delta_band_completeness(
                    points, underlying, tenor_grid,
                    thresholds=thresholds, run_id=run_id, run_ts=run_ts,
                )
            )

    report = build_report(results, run_id=run_id, run_ts=run_ts)
    escalation = escalation_level(report)

    if persist and results:
        store.write(_QC_RESULTS_TABLE, results)

    log.info(
        "orchestration.qc.done",
        overall_status=report.overall_status,
        escalation=escalation,
        fail_count=report.fail_count,
        warn_count=report.warn_count,
        triage_headlines=[
            result_headline(result)
            for result in report.results
            if result.qc_status != STATUS_PASS
        ],
    )
    return QcJobResult(
        correlation_id=correlation_id,
        trade_date=trade_date,
        report=report,
        escalation=escalation,
        results=tuple(results),
    )
