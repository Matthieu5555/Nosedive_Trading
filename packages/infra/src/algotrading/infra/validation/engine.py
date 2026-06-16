from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .anomaly import AnomalyOutcome, AnomalyStatus, AnomalyThresholds, detect_anomalies
from .state import ValidationCheck, ValidationReport, ValidationStatus

_ANOMALY_TO_VALIDATION = {
    AnomalyStatus.NORMAL: ValidationStatus.PASS,
    AnomalyStatus.NO_BASELINE: ValidationStatus.PASS,
    AnomalyStatus.WARN: ValidationStatus.WARN,
    AnomalyStatus.FAIL: ValidationStatus.FAIL,
}

REASON_METRIC_ANOMALY = "metric_anomaly"


@dataclass(frozen=True, slots=True)
class ValidationOutcome:

    report: ValidationReport
    anomalies: tuple[AnomalyOutcome, ...]


def _anomaly_to_check(outcome: AnomalyOutcome) -> ValidationCheck:
    status = _ANOMALY_TO_VALIDATION[outcome.status]
    reason = REASON_METRIC_ANOMALY if status is not ValidationStatus.PASS else None
    return ValidationCheck(
        check=outcome.metric,
        status=status,
        detail=outcome.detail,
        locator=f"metric={outcome.metric}",
        reason_code=reason,
        measured=outcome.value,
    )


def run_validation(
    *,
    run_id: str,
    underlying: str,
    as_of: datetime,
    current_metrics: Mapping[str, float],
    baselines: Mapping[str, Sequence[float]],
    thresholds: AnomalyThresholds,
) -> ValidationOutcome:
    anomalies = detect_anomalies(baselines, current_metrics, thresholds)
    checks = tuple(_anomaly_to_check(a) for a in anomalies)
    report = ValidationReport.from_checks(
        run_id=run_id,
        underlying=underlying,
        as_of=as_of,
        checks=checks,
        threshold_version=thresholds.threshold_version,
    )
    return ValidationOutcome(report=report, anomalies=anomalies)
