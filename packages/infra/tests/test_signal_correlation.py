from __future__ import annotations

import math

import numpy as np
import pytest
from algotrading.infra.risk import basket_variance
from algotrading.infra.signals import ImpliedCorrelationError, implied_correlation


def test_two_name_basket_matches_hand_derivation() -> None:
    index_vol = math.sqrt(0.0432)
    assert implied_correlation([0.6, 0.4], [0.20, 0.30], index_vol) == pytest.approx(0.5)


@pytest.mark.parametrize("rho", [-0.2, 0.0, 0.25, 0.5, 0.85, 1.0])
def test_round_trips_through_forward_basket_variance(rho: float) -> None:
    weights = [0.5, 0.3, 0.2]
    vols = [0.20, 0.25, 0.30]
    forward = basket_variance(weights, vols, avg_correlation=rho)
    assert implied_correlation(weights, vols, forward.vol) == pytest.approx(rho, abs=1e-12)


def test_correlation_above_one_is_not_clamped() -> None:
    weights = [0.5, 0.5]
    vols = [0.20, 0.20]
    fully_correlated_vol = 0.20
    rho_bar = implied_correlation(weights, vols, fully_correlated_vol + 0.05)
    assert rho_bar > 1.0


def test_single_name_basket_is_degenerate() -> None:
    with pytest.raises(ImpliedCorrelationError) as excinfo:
        implied_correlation([1.0], [0.20], 0.20)
    assert excinfo.value.cross == pytest.approx(0.0)


def test_all_zero_vol_basket_is_degenerate() -> None:
    with pytest.raises(ImpliedCorrelationError):
        implied_correlation([0.6, 0.4], [0.0, 0.0], 0.0)


@pytest.mark.parametrize(
    ("weights", "vols", "index_vol"),
    [
        ([0.6, 0.4], [0.20], 0.20),
        ([], [], 0.20),
        ([0.6, 0.4], [0.20, -0.30], 0.20),
        ([0.6, 0.4], [0.20, 0.30], -0.20),
    ],
)
def test_malformed_inputs_raise_value_error(
    weights: list[float], vols: list[float], index_vol: float
) -> None:
    with pytest.raises(ValueError):
        implied_correlation(weights, vols, index_vol)


def test_normalized_weights_yield_a_plausible_correlation_in_unit_interval() -> None:
    # Regression for the un-normalized-weight bug: passing percentage weights (summing to ~95)
    # blew the own/cross terms up ~1000x and drove ρ̄ to an impossible negative. With weights
    # normalized to sum to 1.0 the closed form returns a plausible reading strictly inside (0, 1).
    raw_pct = [40.0, 30.0, 15.0, 10.0]
    total = sum(raw_pct)
    weights = [w / total for w in raw_pct]
    vols = [0.18, 0.22, 0.25, 0.30]
    # A single-name realized-vol baseline below the constituents, the SX5E-shaped hybrid leg.
    index_vol = 0.13
    rho_bar = implied_correlation(weights, vols, index_vol)
    assert 0.0 < rho_bar < 1.0


def test_un_normalized_percentage_weights_go_out_of_band() -> None:
    # Documents the bug's signature: the SAME basket fed as raw percentages returns a value far
    # outside (0, 1) (here strongly negative), which is what the production fix prevents.
    raw_pct = [40.0, 30.0, 15.0, 10.0]
    vols = [0.18, 0.22, 0.25, 0.30]
    index_vol = 0.13
    rho_bar = implied_correlation(raw_pct, vols, index_vol)
    assert not (0.0 < rho_bar < 1.0)


def test_weight_order_is_respected() -> None:
    a = implied_correlation([0.7, 0.3], [0.20, 0.30], math.sqrt(0.0432))
    b = implied_correlation([0.3, 0.7], [0.20, 0.30], math.sqrt(0.0432))
    assert not np.isclose(a, b)
