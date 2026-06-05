"""The validation plane: rolling-baseline anomaly detection + the validation pass.

The robust z-score is checked against a hand computation done in the test comments — an
oracle independent of the code under test (tasks/TESTING.md). The chosen baseline has a
non-degenerate MAD so the arithmetic is exact and the band boundaries are pinned. The
engine tests then pin that a run scores into a located, deterministic report.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from algotrading.infra.validation import (
    AnomalyOutcome,
    AnomalyStatus,
    AnomalyThresholds,
    ValidationCheck,
    ValidationOutcome,
    ValidationStatus,
    detect_anomalies,
    detect_anomaly,
    robust_zscore_vs_baseline,
    run_validation,
)
from hypothesis import given
from hypothesis import strategies as st

# A 12-point baseline with a hand-computed robust scale, reused across the band tests.
#   median(BASELINE) = 15.5
#   abs deviations sorted = [0.5,0.5,1.5,1.5,2.5,2.5,3.5,3.5,4.5,4.5,5.5,5.5] -> MAD = 3.0
#   robust scale = 1.4826 * 3.0 = 4.4478
# so robust_z(value) = (value - 15.5) / 4.4478
BASELINE = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0]
_SCALE = 1.4826 * 3.0

# Default bands: warn at |z| >= 3.5, fail at |z| >= 5.0, judge only with >= 10 history.
THRESHOLDS = AnomalyThresholds()

RUN_ID = "val-run-2026-06-02"
AS_OF = datetime(2026, 6, 2, 23, 30, tzinfo=UTC)


# ================================================================================
# anomaly detection
# ================================================================================
def test_robust_zscore_matches_hand_computation() -> None:
    # value 33 -> (33 - 15.5) / 4.4478 = 3.9345 (hand-computed, independent of the code).
    z = robust_zscore_vs_baseline(33.0, BASELINE)
    assert z == pytest.approx(17.5 / _SCALE, rel=1e-9)
    assert z == pytest.approx(3.9345, abs=1e-3)


def test_on_median_scores_zero_and_is_normal() -> None:
    outcome = detect_anomaly("m", BASELINE, 15.5, THRESHOLDS)
    assert outcome.robust_z == pytest.approx(0.0)
    assert outcome.status is AnomalyStatus.NORMAL


def test_normal_series_does_not_flag() -> None:
    # value 28 -> 12.5 / 4.4478 = 2.81, below the warn band -> NORMAL.
    outcome = detect_anomaly("m", BASELINE, 28.0, THRESHOLDS)
    assert outcome.status is AnomalyStatus.NORMAL


def test_spike_flags_warn_in_warn_band() -> None:
    # value 33 -> z 3.9345, in [warn_z=3.5, fail_z=5.0) -> WARN.
    outcome = detect_anomaly("m", BASELINE, 33.0, THRESHOLDS)
    assert outcome.status is AnomalyStatus.WARN
    assert outcome.robust_z is not None and outcome.robust_z > 0


def test_spike_flags_fail_above_fail_band() -> None:
    # value 40 -> 24.5 / 4.4478 = 5.508 >= fail_z=5.0 -> FAIL.
    outcome = detect_anomaly("m", BASELINE, 40.0, THRESHOLDS)
    assert outcome.status is AnomalyStatus.FAIL


def test_downward_spike_flags_on_magnitude() -> None:
    # A collapse is as anomalous as a blow-out: value -8 -> -23.5/4.4478 = -5.28 -> FAIL.
    out = detect_anomaly("m", BASELINE, -8.0, THRESHOLDS)
    assert out.status is AnomalyStatus.FAIL
    assert out.robust_z is not None and out.robust_z < 0  # sign is preserved


def test_boundary_value_exactly_on_warn_band_warns() -> None:
    # Build a value whose |z| is exactly warn_z: value = 15.5 + warn_z * scale.
    on_warn = 15.5 + THRESHOLDS.warn_z * _SCALE
    assert detect_anomaly("m", BASELINE, on_warn, THRESHOLDS).status is AnomalyStatus.WARN


def test_too_little_history_is_no_baseline_not_normal() -> None:
    short = BASELINE[:5]  # 5 < min_baseline (10)
    outcome = detect_anomaly("m", short, 1000.0, THRESHOLDS)
    assert outcome.status is AnomalyStatus.NO_BASELINE
    assert outcome.robust_z is None  # cannot judge -> carries no score
    assert outcome.baseline_n == 5


def test_no_baseline_outcome_rejects_a_score() -> None:
    with pytest.raises(ValueError, match="NO_BASELINE"):
        AnomalyOutcome("m", AnomalyStatus.NO_BASELINE, 1.0, robust_z=2.0, baseline_n=0, detail="x")


def test_judged_outcome_requires_a_score() -> None:
    with pytest.raises(ValueError, match="robust_z"):
        AnomalyOutcome("m", AnomalyStatus.FAIL, 1.0, robust_z=None, baseline_n=12, detail="x")


def test_degenerate_baseline_scores_inf_off_median_zero_on() -> None:
    flat = [7.0] * 12
    assert robust_zscore_vs_baseline(7.0, flat) == 0.0
    assert robust_zscore_vs_baseline(9.0, flat) == math.inf
    assert robust_zscore_vs_baseline(5.0, flat) == -math.inf
    # An off-median value against a no-spread baseline is an unambiguous FAIL.
    assert detect_anomaly("m", flat, 9.0, THRESHOLDS).status is AnomalyStatus.FAIL


def test_thresholds_reject_inverted_bands() -> None:
    with pytest.raises(ValueError, match="fail_z"):
        AnomalyThresholds(warn_z=5.0, fail_z=3.0)


def test_thresholds_reject_zero_min_baseline() -> None:
    with pytest.raises(ValueError, match="min_baseline"):
        AnomalyThresholds(min_baseline=0)


def test_detect_anomalies_is_sorted_and_order_invariant() -> None:
    baselines = {"a_metric": BASELINE, "z_metric": BASELINE}
    # Two insertion orders of the same current metrics must give identical output.
    one = detect_anomalies(baselines, {"z_metric": 15.5, "a_metric": 40.0}, THRESHOLDS)
    two = detect_anomalies(baselines, {"a_metric": 40.0, "z_metric": 15.5}, THRESHOLDS)
    assert one == two
    assert [o.metric for o in one] == ["a_metric", "z_metric"]  # sorted, deterministic


def test_detect_anomalies_missing_baseline_is_no_baseline() -> None:
    out = detect_anomalies({}, {"new_metric": 5.0}, THRESHOLDS)
    assert out[0].status is AnomalyStatus.NO_BASELINE


def test_detect_anomalies_empty_current_is_empty() -> None:
    assert detect_anomalies({"a": BASELINE}, {}, THRESHOLDS) == ()


@given(value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_property_median_value_never_flags(value: float) -> None:
    # For any baseline with spread, a value equal to its median scores |z| = 0, which can
    # never exceed a positive warn band -> always NORMAL. (Well-defined, monotone scale.)
    median = 15.5
    outcome = detect_anomaly("m", BASELINE, median, THRESHOLDS)
    assert outcome.status is AnomalyStatus.NORMAL
    # And a value further from the median never scores a *smaller* magnitude.
    nearer = robust_zscore_vs_baseline(median + abs(value) * 0.5, BASELINE)
    further = robust_zscore_vs_baseline(median + abs(value), BASELINE)
    assert abs(further) >= abs(nearer)


# ================================================================================
# the validation pass
# ================================================================================
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
