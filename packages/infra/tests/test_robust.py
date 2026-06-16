import math

import pytest
from algotrading.infra.utils.robust import (
    MAD_SCALE,
    median_absolute_deviation,
    outlier_flags,
    robust_zscore_vs_baseline,
    robust_zscores,
    theil_sen_line,
    weighted_median,
)


def test_mad_is_median_of_absolute_deviations():
    assert median_absolute_deviation([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(1.0)


def test_mad_is_zero_when_all_values_equal():
    assert median_absolute_deviation([7.0, 7.0, 7.0]) == pytest.approx(0.0)


def test_mad_empty_is_zero():
    assert median_absolute_deviation([]) == 0.0


def test_zscore_matches_equation_24():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    median, mad = 3.0, 1.0
    scores = robust_zscores(values)
    expected = [(v - median) / (MAD_SCALE * mad) for v in values]
    assert scores == pytest.approx(expected)


def test_zscore_is_zero_at_the_median():
    scores = robust_zscores([1.0, 2.0, 3.0, 4.0, 5.0])
    assert scores[2] == pytest.approx(0.0)


def test_zscore_is_none_for_every_value_when_mad_is_zero():
    assert robust_zscores([4.0, 4.0, 4.0, 4.0]) == (None, None, None, None)


def test_baseline_zscore_scores_external_value():
    z = robust_zscore_vs_baseline(8.0, [1.0, 2.0, 3.0, 4.0, 5.0])
    assert z == pytest.approx(5.0 / 1.4826)


def test_baseline_zscore_zero_at_baseline_median():
    assert robust_zscore_vs_baseline(3.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(0.0)


def test_baseline_zscore_flat_baseline_is_unbounded():
    assert robust_zscore_vs_baseline(10.0, [5.0, 5.0, 5.0]) == math.inf
    assert robust_zscore_vs_baseline(1.0, [5.0, 5.0, 5.0]) == -math.inf
    assert robust_zscore_vs_baseline(5.0, [5.0, 5.0, 5.0]) == 0.0


def test_outlier_flags_catches_gross_residual():
    residuals = [0.0, 0.1, -0.1, 0.05, -0.05, 5.0]
    flags = outlier_flags(residuals)
    assert flags == (False, False, False, False, False, True)


def test_outlier_flags_floor_prevents_spurious_rejection_on_clean_fit():
    residuals = [1e-16, -1e-16, 2e-16, -2e-16, 0.0]
    assert outlier_flags(residuals, scale_floor=1e-6) == (False, False, False, False, False)


def test_outlier_flags_too_few_points_flags_nothing():
    assert outlier_flags([0.0, 100.0]) == (False, False)


def test_outlier_flags_rejection_z_tunes_the_cutoff():
    residuals = [0.0, 0.1, -0.1, 0.05, -0.05, 5.0]
    assert outlier_flags(residuals) == outlier_flags(residuals, rejection_z=3.5)
    assert outlier_flags(residuals, rejection_z=1000.0) == (False,) * 6
    assert outlier_flags(residuals, rejection_z=1.0)[-1] is True


def test_theil_sen_recovers_exact_line():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [1.0, 3.0, 5.0, 7.0]
    slope, intercept = theil_sen_line(xs, ys)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)


def test_theil_sen_ignores_a_minority_outlier():
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    ys = [0.0, 1.0, 2.0, 50.0, 4.0]
    slope, intercept = theil_sen_line(xs, ys)
    assert slope == pytest.approx(1.0)
    assert intercept == pytest.approx(0.0)


def test_theil_sen_no_distinct_pair_raises():
    with pytest.raises(ValueError, match="no distinct-x pair"):
        theil_sen_line([2.0, 2.0, 2.0], [1.0, 2.0, 3.0])


def test_weighted_median_equal_weights_is_median_crossing():
    assert weighted_median([1.0, 2.0, 3.0], [1.0, 1.0, 1.0]) == pytest.approx(2.0)


def test_weighted_median_shifts_toward_heavy_value():
    assert weighted_median([1.0, 2.0, 3.0], [1.0, 1.0, 5.0]) == pytest.approx(3.0)


def test_weighted_median_single_value():
    assert weighted_median([5.0], [2.0]) == pytest.approx(5.0)


def test_weighted_median_is_order_independent():
    a = weighted_median([3.0, 1.0, 2.0], [5.0, 1.0, 1.0])
    b = weighted_median([1.0, 2.0, 3.0], [1.0, 1.0, 5.0])
    assert a == pytest.approx(b)


def test_weighted_median_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        weighted_median([], [])
