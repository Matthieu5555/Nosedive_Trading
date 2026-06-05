"""The validation pass: score a run's tracked metrics and roll them into a report.

This is the pure entry point for the validation plane, the analogue of the QC plane's
check library. It is handed a run's tracked metrics and their rolling baselines (already
assembled by the caller — the metrics live in the analytics outputs, and the baselines
come from storage; neither is read here, keeping this a pure function of its inputs), it
scores each metric for anomalies, and it expresses every score as a
:class:`~validation.state.ValidationCheck` so the run rolls up into one
:class:`~validation.state.ValidationReport`.

The report and the raw anomaly outcomes travel together in a :class:`ValidationOutcome`,
because the triage layer needs both: the report for the run's identity and worst status,
the outcomes for the per-metric specifics it turns into triage rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .anomaly import AnomalyOutcome, AnomalyStatus, AnomalyThresholds, detect_anomalies
from .state import ValidationCheck, ValidationReport, ValidationStatus

# How an anomaly verdict maps onto a validation status. NO_BASELINE is a PASS: a metric we
# cannot yet judge is not a flag to act on (the operator awaits history), but it is kept as
# a PASS check so the report still records that the metric was looked at.
_ANOMALY_TO_VALIDATION = {
    AnomalyStatus.NORMAL: ValidationStatus.PASS,
    AnomalyStatus.NO_BASELINE: ValidationStatus.PASS,
    AnomalyStatus.WARN: ValidationStatus.WARN,
    AnomalyStatus.FAIL: ValidationStatus.FAIL,
}

# The single reason code every metric-anomaly flag carries, so a reporting layer can group
# anomaly flags without parsing the detail string.
REASON_METRIC_ANOMALY = "metric_anomaly"


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """The whole result of one validation pass: the rolled-up report and the raw scores."""

    report: ValidationReport
    anomalies: tuple[AnomalyOutcome, ...]


def _anomaly_to_check(outcome: AnomalyOutcome) -> ValidationCheck:
    """Express one anomaly score as a located, explained validation check."""
    status = _ANOMALY_TO_VALIDATION[outcome.status]
    # Only a real flag (WARN/FAIL) carries a reason code; a PASS (normal or no-baseline)
    # has none, which the ValidationCheck contract requires.
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
    """Score a run's metrics against their baselines and roll them into one report.

    ``run_id``/``as_of`` are injected, never read from a clock, so the outcome reproduces
    in replay. The report's checks are in sorted metric order (from
    :func:`~validation.anomaly.detect_anomalies`), so a stored report is deterministic
    regardless of metric insertion order.
    """
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
