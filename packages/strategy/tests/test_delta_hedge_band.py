from __future__ import annotations

import pytest
from algotrading.strategy import (
    DeltaHedgeBand,
    DeltaHedgeBandError,
    decide_delta_hedge,
)

COURSE_TARGET = 0.50
COURSE_HALF_WIDTH = 0.06


_CYCLE_CASES = [
    ("at target", 0.50, 0.0, False),
    ("drift up, inside", 0.55, 0.0, False),
    ("drift down, inside", 0.45, 0.0, False),
    ("breach up", 0.60, -0.10, True),
    ("breach down", 0.40, +0.10, True),
    ("hard breach up", 0.80, -0.30, True),
]


@pytest.mark.parametrize(
    ("label", "net_delta", "expected_quantity", "expected_breached"),
    _CYCLE_CASES,
    ids=[c[0] for c in _CYCLE_CASES],
)
def test_course_delta_cycle(
    label: str, net_delta: float, expected_quantity: float, expected_breached: bool
) -> None:
    band = DeltaHedgeBand(target=COURSE_TARGET, half_width=COURSE_HALF_WIDTH)
    instruction = decide_delta_hedge(net_delta, band)
    assert instruction.breached is expected_breached
    assert instruction.hedge_quantity == pytest.approx(expected_quantity)


def test_band_edge_is_inclusive() -> None:
    band = DeltaHedgeBand(target=0.0, half_width=0.25)
    assert decide_delta_hedge(0.25, band).breached is False
    assert decide_delta_hedge(-0.25, band).breached is False
    beyond = decide_delta_hedge(0.5, band)
    assert beyond.breached is True
    assert beyond.hedge_quantity == pytest.approx(-0.5)


def test_flat_book_band_around_zero() -> None:
    band = DeltaHedgeBand(target=0.0, half_width=10.0)
    assert decide_delta_hedge(5.0, band).hedge_quantity == pytest.approx(0.0)
    assert decide_delta_hedge(-5.0, band).hedge_quantity == pytest.approx(0.0)
    breach = decide_delta_hedge(15.0, band)
    assert breach.breached is True
    assert breach.hedge_quantity == pytest.approx(-15.0)


def test_zero_half_width_rehedges_on_any_drift() -> None:
    band = DeltaHedgeBand(target=0.0, half_width=0.0)
    assert decide_delta_hedge(0.0, band).breached is False
    assert decide_delta_hedge(0.0, band).hedge_quantity == pytest.approx(0.0)
    drift = decide_delta_hedge(0.01, band)
    assert drift.breached is True
    assert drift.hedge_quantity == pytest.approx(-0.01)


def test_hedge_ratio_scales_the_quantity() -> None:
    band = DeltaHedgeBand(target=0.0, half_width=5.0, hedge_ratio=-0.5)
    instruction = decide_delta_hedge(20.0, band)
    assert instruction.breached is True
    assert instruction.hedge_quantity == pytest.approx(-10.0)


def test_nonzero_target_sizes_the_excess_over_target() -> None:
    band = DeltaHedgeBand(target=0.5, half_width=0.25)
    instruction = decide_delta_hedge(2.0, band)
    assert instruction.breached is True
    assert instruction.hedge_quantity == pytest.approx(-1.5)


def test_negative_half_width_is_rejected() -> None:
    with pytest.raises(DeltaHedgeBandError) as exc:
        DeltaHedgeBand(half_width=-0.01)
    assert exc.value.field == "half_width"


def test_zero_hedge_ratio_is_rejected() -> None:
    with pytest.raises(DeltaHedgeBandError) as exc:
        DeltaHedgeBand(hedge_ratio=0.0)
    assert exc.value.field == "hedge_ratio"


def test_defaults_are_flat_target_unit_neutralising() -> None:
    band = DeltaHedgeBand()
    assert band.target == 0.0
    assert band.half_width == 0.0
    assert band.hedge_ratio == -1.0
