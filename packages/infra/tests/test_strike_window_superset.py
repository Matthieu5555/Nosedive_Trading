from __future__ import annotations

import math

import pytest
from algotrading.core.config import StrikeSelectionConfig
from algotrading.infra.universe import (
    AvailableChain,
    BandReach,
    ChainSelection,
    DeltaBandMarket,
    StrikeWindowClipError,
    TenorMarket,
    delta_band_window_pct,
    plan_chain,
    select_strikes,
)
from fixtures.synthetic import delta_band_boundary_strike

_FORWARD = 100.0
_DELTA_BOUND = 0.30


def _reach_from_boundary_strikes(*, maturity_years: float, volatility: float) -> float:
    call_edge = delta_band_boundary_strike(
        forward=_FORWARD,
        maturity_years=maturity_years,
        volatility=volatility,
        target_call_nd1=_DELTA_BOUND,
    )
    put_edge = delta_band_boundary_strike(
        forward=_FORWARD,
        maturity_years=maturity_years,
        volatility=volatility,
        target_call_nd1=1.0 - _DELTA_BOUND,
    )
    return max(
        abs(call_edge / _FORWARD - 1.0), abs(1.0 - put_edge / _FORWARD)
    )


@pytest.mark.parametrize(
    ("maturity_years", "volatility"),
    [(0.25, 0.40), (3.0, 0.20), (3.0, 0.40), (1.0, 0.60)],
)
def test_window_pct_matches_independent_boundary_strike_reach(
    maturity_years: float, volatility: float
) -> None:
    expected = _reach_from_boundary_strikes(
        maturity_years=maturity_years, volatility=volatility
    )
    got = delta_band_window_pct(
        delta_bound=_DELTA_BOUND, maturity_years=maturity_years, working_vol=volatility
    )
    assert got == pytest.approx(expected, rel=1e-12)


def test_long_tenor_high_vol_band_exceeds_the_default_window() -> None:
    reach = delta_band_window_pct(
        delta_bound=_DELTA_BOUND, maturity_years=3.0, working_vol=0.40
    )
    assert reach > 0.35


def _high_vol_chain() -> AvailableChain:
    band_edge = delta_band_boundary_strike(
        forward=_FORWARD, maturity_years=3.0, volatility=0.40, target_call_nd1=_DELTA_BOUND
    )
    in_band_above_window = _FORWARD * 1.50
    assert in_band_above_window < band_edge
    strikes = (60.0, 80.0, _FORWARD, 120.0, in_band_above_window, band_edge * 0.99)
    return AvailableChain(
        exchange="SMART",
        trading_class="SPX",
        multiplier="100",
        expirations=("20290612",),
        strikes=strikes,
    )


def test_fallback_fails_loud_instead_of_clipping_the_band_at_high_vol() -> None:
    band_reach = BandReach(delta_bound=_DELTA_BOUND, maturity_years=3.0, working_vol=0.40)
    selection = ChainSelection(strike_window_pct=0.35, min_strikes_per_side=1)
    with pytest.raises(StrikeWindowClipError) as excinfo:
        select_strikes(
            _high_vol_chain().strikes, _FORWARD, selection, band_reach=band_reach
        )
    assert excinfo.value.configured_window_pct == 0.35
    assert excinfo.value.required_window_pct > 0.35


def test_fallback_keeps_in_band_strikes_when_window_is_a_superset() -> None:
    reach = delta_band_window_pct(
        delta_bound=_DELTA_BOUND, maturity_years=3.0, working_vol=0.40
    )
    superset_window = min(1.0, reach + 0.01)
    band_reach = BandReach(delta_bound=_DELTA_BOUND, maturity_years=3.0, working_vol=0.40)
    in_band = _FORWARD * 1.50
    chain_strikes = (60.0, 80.0, _FORWARD, 120.0, in_band)
    selection = ChainSelection(strike_window_pct=superset_window, min_strikes_per_side=1)
    kept = select_strikes(chain_strikes, _FORWARD, selection, band_reach=band_reach)
    assert in_band in kept
    assert all(math.isfinite(strike) for strike in kept)


def test_no_guard_when_band_reach_is_absent() -> None:
    selection = ChainSelection(strike_window_pct=0.35, min_strikes_per_side=1)
    kept = select_strikes((60.0, 80.0, 100.0, 120.0), 100.0, selection)
    assert 100.0 in kept


def test_plan_chain_per_expiry_fallback_fails_loud_on_nonfinite_forward() -> None:
    chain = _high_vol_chain()
    band = DeltaBandMarket(
        selection=StrikeSelectionConfig(version="ss-test", min_strikes_per_side=1),
        markets={
            "20290612": TenorMarket(
                forward=float("nan"),
                maturity_years=3.0,
                volatility=0.40,
                discount_factor=1.0,
            )
        },
    )
    selection = ChainSelection(
        max_expiries=None, strike_window_pct=0.35, min_strikes_per_side=1
    )
    with pytest.raises(StrikeWindowClipError):
        plan_chain("SPX", [chain], spot=_FORWARD, selection=selection, band=band)
