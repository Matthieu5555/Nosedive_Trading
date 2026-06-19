from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
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

    correlation_id: str
    trade_date: date
    report: QcReport
    escalation: str
    results: tuple[QcResult, ...] = field(default_factory=tuple)

    @property
    def overall_status(self) -> str:
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
    index_symbols: Collection[str] | None = None,
) -> QcJobResult:
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

    if grid_points is not None:
        for underlying in sorted(grid_points):
            points = grid_points[underlying]
            # Scope-aware QC severity (ADR 0060): a grid coverage/band collapse on the tradeable
            # index stays CRITICAL (pages, blocks banking); the same on an illiquid single-name
            # constituent is notice-level. index_symbols=None keeps every underlying strict.
            is_index = index_symbols is None or underlying in index_symbols
            results.append(
                check_tenor_coverage_floor(
                    points, underlying, tenor_grid,
                    thresholds=thresholds, run_id=run_id, run_ts=run_ts,
                    is_index=is_index,
                )
            )
            results.append(
                check_delta_band_completeness(
                    points, underlying, tenor_grid,
                    thresholds=thresholds, run_id=run_id, run_ts=run_ts,
                    is_index=is_index,
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
