"""The validation plane — is a day's run trustworthy relative to its own history.

The sibling of the QC plane (``src/qc``). QC asks static, per-object questions ("did this
fit pass its RMSE cut-off"); validation asks a rolling-baseline question ("did this run's
metrics shift abnormally versus the recent past"). The two are deliberately separate: a
run can pass every static check and still be anomalous, and that is exactly the failure
this plane catches. Both planes' results then collapse into one triage shape so there is a
single thing to persist, order, and escalate on.

The fast path — score a run and get its triage list:

    from validation import AnomalyThresholds, run_validation, build_triage, escalation_level

    outcome = run_validation(
        run_id=run_id, underlying="AAPL", as_of=as_of,
        current_metrics={"n_iv_points": 412.0, "max_slice_rmse": 0.004, ...},
        baselines={"n_iv_points": [...recent history...], ...},
        thresholds=AnomalyThresholds(),
    )
    records = build_triage(qc_report=qc_report, validation=outcome)  # one unified list
    level = escalation_level(records)                                # none / notice / page

Every entry point takes its ``run_id``/``as_of`` injected — never a clock — so a
validation pass is a pure function of its inputs and reproduces byte-for-byte in replay.
The triage records are pure values; persisting them (to the ``triage_records`` table) is
the orchestration layer's job, not this plane's.
"""

from __future__ import annotations

from .anomaly import (
    AnomalyOutcome,
    AnomalyStatus,
    AnomalyThresholds,
    detect_anomalies,
    detect_anomaly,
    robust_zscore_vs_baseline,
)
from .engine import REASON_METRIC_ANOMALY, ValidationOutcome, run_validation
from .state import (
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
    worst_status,
)
from .triage import (
    SOURCE_QC,
    SOURCE_VALIDATION,
    build_triage,
    escalation_level,
    triage_from_qc,
    triage_from_validation,
)

__all__ = [
    "REASON_METRIC_ANOMALY",
    "SOURCE_QC",
    "SOURCE_VALIDATION",
    "AnomalyOutcome",
    "AnomalyStatus",
    "AnomalyThresholds",
    "ValidationCheck",
    "ValidationOutcome",
    "ValidationReport",
    "ValidationStatus",
    "build_triage",
    "detect_anomalies",
    "detect_anomaly",
    "escalation_level",
    "robust_zscore_vs_baseline",
    "run_validation",
    "triage_from_qc",
    "triage_from_validation",
    "worst_status",
]
