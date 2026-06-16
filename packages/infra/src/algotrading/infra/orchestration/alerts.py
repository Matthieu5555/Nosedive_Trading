from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.infra.qc import (
    CHECK_TENOR_COVERAGE_FLOOR,
    ESCALATION_PAGE,
    STATUS_FAIL,
    QcReport,
    deserialize_context,
    escalation_level,
)

from .run_state import OUTCOME_OK, StageRun

ALERT_COLLECTOR_DEATH = "collector_death"
ALERT_MISSING_PARTITION = "missing_partition"
ALERT_ELEVATED_FAILURE_RATE = "elevated_failure_rate"
ALERT_QC_FAIL = "qc_fail"
ALERT_COVERAGE_BREACH = "coverage_breach"

COLLECTOR_SILENCE_SECONDS = 120.0
MAX_FAILURE_RATIO = 0.5
FAILURE_WINDOW = 6


@dataclass(frozen=True, slots=True)
class Alert:

    kind: str
    subject: str
    detail: str
    detection_interval_seconds: float


def collector_death_alert(
    *,
    session_id: str,
    last_event_ts: datetime | None,
    now: datetime,
    silence_seconds: float = COLLECTOR_SILENCE_SECONDS,
) -> Alert | None:
    if last_event_ts is None:
        return Alert(
            kind=ALERT_COLLECTOR_DEATH,
            subject=session_id,
            detail="no observation ever recorded for session",
            detection_interval_seconds=silence_seconds,
        )
    silent_for = (now - last_event_ts).total_seconds()
    if silent_for >= silence_seconds:
        return Alert(
            kind=ALERT_COLLECTOR_DEATH,
            subject=session_id,
            detail=f"silent for {silent_for:g}s (>= {silence_seconds:g}s)",
            detection_interval_seconds=silence_seconds,
        )
    return None


def missing_partition_alerts(
    *,
    table: str,
    expected: Sequence[tuple[date, str]],
    present: Sequence[tuple[date, str]],
) -> list[Alert]:
    present_set = set(present)
    alerts: list[Alert] = []
    for trade_date, underlying in sorted(set(expected) - present_set):
        alerts.append(
            Alert(
                kind=ALERT_MISSING_PARTITION,
                subject=f"{table} {trade_date.isoformat()}/{underlying}",
                detail="expected analytic partition absent — not interpolated",
                detection_interval_seconds=0.0,
            )
        )
    return alerts


def elevated_failure_rate_alert(
    *,
    runs: Sequence[StageRun],
    window: int = FAILURE_WINDOW,
    max_failure_ratio: float = MAX_FAILURE_RATIO,
) -> Alert | None:
    if len(runs) < window:
        return None
    recent = list(runs)[-window:]
    failed = sum(1 for run in recent if run.outcome != OUTCOME_OK)
    ratio = failed / window
    if ratio > max_failure_ratio:
        return Alert(
            kind=ALERT_ELEVATED_FAILURE_RATE,
            subject=f"last {window} stage runs",
            detail=f"failure ratio {ratio:g} (> {max_failure_ratio:g})",
            detection_interval_seconds=0.0,
        )
    return None


def qc_fail_alert(report: QcReport) -> Alert | None:
    if escalation_level(report) == ESCALATION_PAGE:
        return Alert(
            kind=ALERT_QC_FAIL,
            subject=report.run_id,
            detail=f"QC report escalated to page ({report.fail_count} fail(s))",
            detection_interval_seconds=0.0,
        )
    return None


def coverage_breach_alerts(report: QcReport) -> list[Alert]:
    alerts: list[Alert] = []
    for result in report.results:
        if result.check_name != CHECK_TENOR_COVERAGE_FLOOR or result.qc_status != STATUS_FAIL:
            continue
        context = deserialize_context(result.context)
        underlying = context.get("underlying", result.target_key)
        breaches = context.get("breaching_tenors", [])
        if not isinstance(breaches, list):
            continue
        for breach in breaches:
            if not isinstance(breach, dict):
                continue
            tenor = breach.get("tenor")
            measured = breach.get("measured")
            floor = breach.get("floor")
            alerts.append(
                Alert(
                    kind=ALERT_COVERAGE_BREACH,
                    subject=f"{underlying}@{tenor}",
                    detail=f"coverage {measured} < floor {floor} — partition present but too thin",
                    detection_interval_seconds=0.0,
                )
            )
    return alerts
