"""Unit tests for the monotone total-variance repair (ADR 0062).

The repair floors the EXTRAPOLATED wings of each expiry's smile so total variance never falls with
maturity, while leaving marks inside observed strike support untouched (a genuine in-data inversion
stays put so the QC still pages on it). Expected values are derived by hand from the policy, not
read back from the implementation.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from algotrading.infra.surfaces import (
    RepairSlice,
    monotone_variance_floor,
    repair_overrides_from_slices,
)

GRID = (-0.2, -0.1, 0.0, 0.1, 0.2)


def _const(value: float) -> Callable[[float], float]:
    return lambda _k: value


def _piecewise(in_support: float, wing: float) -> Callable[[float], float]:
    """``in_support`` on the observed [-0.1, 0.1] core, ``wing`` on the +/-0.2 extrapolated wings."""
    return lambda k: wing if abs(k) > 0.1 else in_support


def test_disabled_returns_empty_so_the_surface_is_raw() -> None:
    slices = [RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1)]
    assert repair_overrides_from_slices(slices, GRID, enabled=False) == {}


def test_enabled_matches_the_direct_floor() -> None:
    slices = [
        RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1),
        RepairSlice("X", 0.10, _piecewise(0.0045, 0.001), -0.1, 0.1),
    ]
    assert repair_overrides_from_slices(slices, GRID, enabled=True) == monotone_variance_floor(
        slices, GRID
    )


def test_shortest_expiry_is_never_floored() -> None:
    # The shortest expiry has no prior curve to floor against, so even its extrapolated wings are
    # served exactly as fit.
    slices = [RepairSlice("X", 0.05, _piecewise(0.004, 0.001), -0.1, 0.1)]
    repaired = monotone_variance_floor(slices, GRID)[("X", 0.05)]
    assert repaired == {-0.2: 0.001, -0.1: 0.004, 0.0: 0.004, 0.1: 0.004, 0.2: 0.001}


def test_extrapolated_inversion_is_floored_up_to_the_shorter_expiry() -> None:
    short = RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1)
    # Longer expiry: healthy 0.0045 in the observed core, but a fabricated 0.001 in the wings that
    # sits BELOW the shorter expiry — a pure extrapolated calendar inversion.
    long = RepairSlice("X", 0.10, _piecewise(0.0045, 0.001), -0.1, 0.1)
    repaired = monotone_variance_floor([short, long], GRID)[("X", 0.10)]

    # Wings are clamped up to the shorter expiry (0.004); the observed core is left untouched.
    assert repaired == {-0.2: 0.004, -0.1: 0.0045, 0.0: 0.0045, 0.1: 0.0045, 0.2: 0.004}
    # Calendar monotonicity is restored everywhere against the shorter expiry.
    short_curve = monotone_variance_floor([short, long], GRID)[("X", 0.05)]
    assert all(repaired[k] >= short_curve[k] - 1e-12 for k in GRID)


def test_a_genuine_in_support_inversion_is_left_for_the_qc_to_page() -> None:
    short = RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1)
    # Longer expiry dips BELOW the shorter one inside observed support: a real, in-data inversion.
    long = RepairSlice("X", 0.10, _const(0.0035), -0.1, 0.1)
    repaired = monotone_variance_floor([short, long], GRID)[("X", 0.10)]

    # The repair must not rewrite it: the in-support marks stay at the raw 0.0035 (still inverted),
    # so the calendar QC still sees and pages on it. Only the extrapolated wings are floored.
    assert repaired[-0.1] == 0.0035
    assert repaired[0.0] == 0.0035
    assert repaired[0.1] == 0.0035
    assert repaired[-0.2] == 0.004
    assert repaired[0.2] == 0.004


def test_an_already_monotone_extrapolation_is_unchanged() -> None:
    short = RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1)
    long = RepairSlice("X", 0.10, _const(0.006), -0.1, 0.1)  # above the shorter expiry everywhere
    repaired = monotone_variance_floor([short, long], GRID)[("X", 0.10)]
    assert repaired == {k: 0.006 for k in GRID}


def test_the_floor_chains_across_three_expiries() -> None:
    # B's wing (0.001) is floored up to A (0.004); C's wing (0.002) must then floor against B's
    # REPAIRED 0.004, not its own raw 0.002 — the floor propagates short -> long.
    a = RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1)
    b = RepairSlice("X", 0.10, _piecewise(0.005, 0.001), -0.1, 0.1)
    c = RepairSlice("X", 0.20, _piecewise(0.006, 0.002), -0.1, 0.1)
    floored = monotone_variance_floor([a, b, c], GRID)
    assert floored[("X", 0.10)][0.2] == 0.004
    assert floored[("X", 0.20)][0.2] == 0.004  # chained through B, not the raw 0.002


def test_underlyings_are_floored_independently() -> None:
    slices = [
        RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1),
        RepairSlice("Y", 0.10, _piecewise(0.003, 0.0005), -0.1, 0.1),
    ]
    floored = monotone_variance_floor(slices, GRID)
    # Y's shortest expiry must not be floored against X's curve.
    assert floored[("Y", 0.10)][0.2] == 0.0005


def test_a_slice_without_observed_support_is_floored_everywhere() -> None:
    short = RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1)
    long = RepairSlice("X", 0.10, _const(0.001), None, None)  # no observed strikes at all
    repaired = monotone_variance_floor([short, long], GRID)[("X", 0.10)]
    # With no support to protect, every grid point is treated as extrapolated and floored up.
    assert repaired == {k: 0.004 for k in GRID}


def test_support_epsilon_widens_the_protected_core() -> None:
    short = RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1)
    long = RepairSlice("X", 0.10, _const(0.001), -0.1, 0.1)
    # k=0.2 is 0.1 outside the observed max; a generous epsilon brings it inside the protected core,
    # so it is NOT floored and keeps its raw (inverted) 0.001.
    repaired = monotone_variance_floor([short, long], GRID, support_epsilon=0.15)[("X", 0.10)]
    assert repaired[0.2] == 0.001


@pytest.mark.parametrize("enabled", [True, False])
def test_gate_is_pure_passthrough_of_the_flag(enabled: bool) -> None:
    slices = [
        RepairSlice("X", 0.05, _const(0.004), -0.1, 0.1),
        RepairSlice("X", 0.10, _piecewise(0.0045, 0.001), -0.1, 0.1),
    ]
    result = repair_overrides_from_slices(slices, GRID, enabled=enabled)
    assert bool(result) is enabled
