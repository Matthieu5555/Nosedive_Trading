from __future__ import annotations

import pytest
from algotrading.infra.signals import IvRankError, iv_percentile, iv_rank


def test_rank_is_min_max_position() -> None:
    assert iv_rank(0.25, [0.10, 0.20, 0.30]) == pytest.approx(0.75)


def test_rank_at_extremes_is_zero_and_one() -> None:
    assert iv_rank(0.10, [0.10, 0.20, 0.30]) == pytest.approx(0.0)
    assert iv_rank(0.30, [0.10, 0.20, 0.30]) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("current", "expected"),
    [(0.05, 0.0), (0.40, 1.0)],
)
def test_rank_clamps_outside_the_window(current: float, expected: float) -> None:
    assert iv_rank(current, [0.10, 0.20, 0.30]) == pytest.approx(expected)


def test_flat_window_is_refused() -> None:
    with pytest.raises(IvRankError):
        iv_rank(0.20, [0.20, 0.20, 0.20])


def test_empty_window_is_refused() -> None:
    with pytest.raises(IvRankError):
        iv_rank(0.20, [])


def test_percentile_is_fraction_strictly_below() -> None:
    assert iv_percentile(0.25, [0.10, 0.20, 0.30, 0.40]) == pytest.approx(0.5)


def test_percentile_at_or_below_all_is_zero() -> None:
    assert iv_percentile(0.05, [0.10, 0.20, 0.30]) == pytest.approx(0.0)


def test_percentile_above_all_is_one() -> None:
    assert iv_percentile(0.99, [0.10, 0.20, 0.30]) == pytest.approx(1.0)


def test_percentile_empty_window_is_refused() -> None:
    with pytest.raises(IvRankError):
        iv_percentile(0.20, [])
