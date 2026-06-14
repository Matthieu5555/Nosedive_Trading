"""Tests for the shared delta-hedge-band rule (course req #9, "Delta-hedge en bande").

Every expected value is derived **by hand from the course rule**, never read back from the code
under test: inside the band ``[target - half_width, target + half_width]`` the rule holds (zero
quantity); on band exit it returns ``hedge_ratio * (net_delta - target)`` — the quantity that
brings net delta back to the target. The headline case walks the course's |Δ| cycle (an ATM
straddle held in a band, re-hedged only when it leaves the band).
"""

from __future__ import annotations

import pytest
from algotrading.strategy import (
    DeltaHedgeBand,
    DeltaHedgeBandError,
    decide_delta_hedge,
)

# The course's worked example: an ATM straddle's delta is kept near its target within a ~±0.06
# tolerance and re-hedged only on band exit (transcript § "Delta-hedge en bande"). These are the
# fixture's hand-chosen target/half-width, not production config.
COURSE_TARGET = 0.50
COURSE_HALF_WIDTH = 0.06


# --- the course |Δ| cycle: hold inside the band, re-hedge only on band exit -------------------
# Each row is (net_delta, expected_hedge_quantity, expected_breached), worked by hand:
#   inside  -> 0.0, not breached, when |net_delta - 0.50| <= 0.06
#   outside -> -1 * (net_delta - 0.50), breached, otherwise (hedge_ratio defaults to -1).
_CYCLE_CASES = [
    ("at target", 0.50, 0.0, False),  # |0.00| <= 0.06
    ("drift up, inside", 0.55, 0.0, False),  # |0.05| <= 0.06
    ("drift down, inside", 0.45, 0.0, False),  # |0.05| <= 0.06
    ("breach up", 0.60, -0.10, True),  # -1 * (0.60 - 0.50) = -0.10
    ("breach down", 0.40, +0.10, True),  # -1 * (0.40 - 0.50) = +0.10
    ("hard breach up", 0.80, -0.30, True),  # -1 * (0.80 - 0.50) = -0.30
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
    # Net delta exactly on the band edge holds (the rule uses <=, so the threshold itself does
    # not trade). Values chosen to be exactly representable in float: target 0.0, half_width
    # 0.25, edge at +/-0.25; just beyond breaches.
    band = DeltaHedgeBand(target=0.0, half_width=0.25)
    assert decide_delta_hedge(0.25, band).breached is False
    assert decide_delta_hedge(-0.25, band).breached is False
    beyond = decide_delta_hedge(0.5, band)  # |0.5| > 0.25
    assert beyond.breached is True
    assert beyond.hedge_quantity == pytest.approx(-0.5)


def test_flat_book_band_around_zero() -> None:
    # S1's case: a delta-flat book (target 0) with a wide band. Inside -> hold; outside -> the
    # full -net_delta neutralising quantity. Derived by hand from the rule with target 0.
    band = DeltaHedgeBand(target=0.0, half_width=10.0)
    assert decide_delta_hedge(5.0, band).hedge_quantity == pytest.approx(0.0)
    assert decide_delta_hedge(-5.0, band).hedge_quantity == pytest.approx(0.0)
    breach = decide_delta_hedge(15.0, band)
    assert breach.breached is True
    assert breach.hedge_quantity == pytest.approx(-15.0)  # -1 * (15.0 - 0.0)


def test_zero_half_width_rehedges_on_any_drift() -> None:
    # half_width 0 -> the band is the single point {target}; any net delta off target breaches.
    band = DeltaHedgeBand(target=0.0, half_width=0.0)
    assert decide_delta_hedge(0.0, band).breached is False
    assert decide_delta_hedge(0.0, band).hedge_quantity == pytest.approx(0.0)
    drift = decide_delta_hedge(0.01, band)
    assert drift.breached is True
    assert drift.hedge_quantity == pytest.approx(-0.01)  # -1 * (0.01 - 0.0)


def test_hedge_ratio_scales_the_quantity() -> None:
    # A hedge instrument carrying 2 delta-units each is sized by hedge_ratio = -0.5: a net delta
    # of 20 (target 0, band 5 -> breached) is closed by -0.5 * 20 = -10 units of the instrument.
    band = DeltaHedgeBand(target=0.0, half_width=5.0, hedge_ratio=-0.5)
    instruction = decide_delta_hedge(20.0, band)
    assert instruction.breached is True
    assert instruction.hedge_quantity == pytest.approx(-10.0)


def test_nonzero_target_sizes_the_excess_over_target() -> None:
    # A deliberate +0.5 directional tilt: only the excess over target is hedged, not the whole
    # delta. net 2.0, target 0.5, band 0.25 -> breached, hedge -1 * (2.0 - 0.5) = -1.5.
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
    # The default band is S1's: flat target, zero tolerance, unit delta-neutralising hedge.
    band = DeltaHedgeBand()
    assert band.target == 0.0
    assert band.half_width == 0.0
    assert band.hedge_ratio == -1.0
