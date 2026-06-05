"""The validation pass: scoring a run's metrics into a located, deterministic report."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from validation import (
    AnomalyThresholds,
    ValidationCheck,
    ValidationOutcome,
    ValidationStatus,
    run_validation,
)

RUN_ID = "val-run-2026-06-02"
AS_OF = datetime(2026, 6, 2, 23, 30, tzinfo=UTC)

# Baseline with a hand-computed robust scale (see test_anomaly): median 15.5, MAD 3.0,
# scale 4.4478. value 40 -> z 5.51 (FAIL); value 15.5 -> z 0 (PASS).
BASELINE = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0]
THRESHOLDS = AnomalyThresholds()


def _run(current: dict[str, float], baselines: dict[str, list[float]]) -> ValidationOutcome:
    return run_validation(
        run_id=RUN_ID,
        underlying="AAPL",
        as_of=AS_OF,
        current_metrics=current,
        baselines=baselines,
        thresholds=THRESHOLDS,
    )


def test_normal_run_is_a_clean_pass() -> None:
    baselines = {"n_iv_points": BASELINE, "max_rmse": BASELINE}
    outcome = _run({"n_iv_points": 15.5, "max_rmse": 15.5}, baselines)
    assert outcome.report.status is ValidationStatus.PASS
    assert outcome.report.failures() == ()


def test_spike_fails_and_locates_the_metric() -> None:
    baselines = {"n_iv_points": BASELINE, "max_rmse": BASELINE}
    outcome = _run({"n_iv_points": 40.0, "max_rmse": 15.5}, baselines)
    assert outcome.report.status is ValidationStatus.FAIL
    failures = outcome.report.failures()
    assert len(failures) == 1
    flagged = failures[0]
    # The flag names the specific metric that moved, not a generic "run anomalous".
    assert flagged.check == "n_iv_points"
    assert flagged.locator == "metric=n_iv_points"
    assert flagged.reason_code == "metric_anomaly"
    assert flagged.measured == 40.0


def test_no_baseline_metric_is_a_pass_check_not_a_flag() -> None:
    # A metric with too little history is recorded (it was looked at) but is not a flag.
    outcome = _run({"new_metric": 999.0}, {})
    assert outcome.report.status is ValidationStatus.PASS
    assert outcome.report.failures() == ()
    assert len(outcome.report.checks) == 1
    assert outcome.report.checks[0].status is ValidationStatus.PASS


def test_report_checks_are_in_sorted_metric_order_and_order_invariant() -> None:
    baselines = {"a": BASELINE, "z": BASELINE}
    one = _run({"z": 15.5, "a": 15.5}, baselines)
    two = _run({"a": 15.5, "z": 15.5}, baselines)
    # Two insertion orders of the same metrics produce an identical report (determinism).
    assert one.report == two.report
    assert [c.check for c in one.report.checks] == ["a", "z"]


def test_report_carries_threshold_version() -> None:
    outcome = _run({"n_iv_points": 15.5}, {"n_iv_points": BASELINE})
    assert outcome.report.threshold_version == THRESHOLDS.threshold_version


def test_validation_check_rejects_unexplained_flag() -> None:
    # A non-PASS check without a reason code is the banner this plane forbids.
    with pytest.raises(ValueError, match="reason_code"):
        ValidationCheck("m", ValidationStatus.FAIL, "moved a lot", reason_code=None)
