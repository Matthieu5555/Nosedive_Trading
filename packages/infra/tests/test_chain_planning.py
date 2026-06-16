from __future__ import annotations

import math

import pytest
from algotrading.core.config import StrikeSelectionConfig, load_platform_config
from algotrading.infra.universe import (
    StrikeSelectionError,
    discovery_delta_bound,
    select_discovery_strikes,
    select_strikes,
    select_strikes_delta_band,
)
from fixtures.synthetic import build_delta_band_ladder, delta_band_boundary_strike


def _cfg(
    *,
    delta_bound: float = 0.30,
    delta_convention: str = "forward_undiscounted",
    min_strikes_per_side: int = 1,
) -> StrikeSelectionConfig:
    return StrikeSelectionConfig(
        version="strike-selection-test",
        delta_bound=delta_bound,
        delta_convention=delta_convention,
        min_strikes_per_side=min_strikes_per_side,
    )


def test_delta_band_spans_30d_put_to_30d_call() -> None:
    ladder = build_delta_band_ladder()
    expected = ladder.expected_band()
    got = select_strikes_delta_band(
        ladder.strikes,
        forward=ladder.forward,
        maturity_years=ladder.maturity_years,
        discount_factor=ladder.discount_factor,
        volatility=ladder.volatility,
        selection=_cfg(),
    )
    assert got == expected
    assert min(got) >= ladder.put_boundary - 1e-9 * ladder.forward
    assert max(got) <= ladder.call_boundary + 1e-9 * ladder.forward
    assert 80.0 not in got and 120.0 not in got


def test_count_varies_with_listing_density() -> None:
    forward, maturity, vol, df = 100.0, 0.25, 0.20, 0.99
    put_b = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.70
    )
    call_b = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.30
    )
    dense = tuple(float(k) for k in range(int(put_b) - 2, int(call_b) + 3))
    sparse = dense[::5]
    selection = _cfg()
    dense_got = select_strikes_delta_band(
        dense, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=selection,
    )
    sparse_got = select_strikes_delta_band(
        sparse, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=selection,
    )
    assert len(dense_got) > len(sparse_got)
    tol = 1e-9 * forward
    for kept in (*dense_got, *sparse_got):
        assert put_b - tol <= kept <= call_b + tol


def test_band_is_per_tenor() -> None:
    forward, vol, df = 100.0, 0.20, 0.99
    strikes = tuple(float(k) for k in range(80, 121))
    near = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=0.10, discount_factor=df,
        volatility=vol, selection=_cfg(),
    )
    far = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=2.00, discount_factor=df,
        volatility=vol, selection=_cfg(),
    )
    assert near != far
    assert max(far) > max(near)


def test_delta_sign_and_atm_included() -> None:
    forward, maturity, vol, df = 100.0, 0.25, 0.20, 0.99
    atm = forward
    call_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.30
    )
    put_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.70
    )
    call_10 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.10
    )
    put_10 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.90
    )
    strikes = (put_10, put_30, atm, call_30, call_10)
    got = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(),
    )
    assert atm in got
    assert call_30 in got and put_30 in got
    assert call_10 not in got and put_10 not in got


def test_convention_pinned() -> None:
    from algotrading.core.config import ConfigFieldError

    forward, maturity, vol = 100.0, 0.25, 0.20
    df = 0.90
    edge = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.30
    )
    inside_above = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.45
    )
    strikes = (forward, inside_above, edge)
    undiscounted = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(delta_convention="forward_undiscounted"),
    )
    discounted = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(delta_convention="spot_discounted"),
    )
    assert edge in undiscounted
    assert edge not in discounted

    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(
            version="bad", delta_bound=0.30, delta_convention="nonsense",
            min_strikes_per_side=1,
        )


def test_strike_selection_config_validation() -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="", delta_bound=0.30)
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=0.0)
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=1.0)
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=float("nan"))
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=0.30, min_strikes_per_side=0)


def test_shipped_universe_config_carries_the_band() -> None:
    config = load_platform_config("configs")
    selection = config.universe.strike_selection
    assert isinstance(selection, StrikeSelectionConfig)
    assert selection.delta_bound == 0.30
    assert selection.delta_convention == "forward_undiscounted"
    ladder = build_delta_band_ladder()
    got = select_strikes_delta_band(
        ladder.strikes,
        forward=ladder.forward,
        maturity_years=ladder.maturity_years,
        discount_factor=ladder.discount_factor,
        volatility=ladder.volatility,
        selection=selection,
    )
    assert got


def test_empty_strike_list_returns_empty() -> None:
    got = select_strikes_delta_band(
        (), forward=100.0, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(),
    )
    assert got == ()


def test_single_strike_returns_that_strike() -> None:
    got = select_strikes_delta_band(
        (100.0,), forward=100.0, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(min_strikes_per_side=1),
    )
    assert got == (100.0,)


def test_all_wing_ladder_falls_back_to_floor() -> None:
    forward = 100.0
    wings = (40.0, 50.0, 60.0, 160.0, 170.0, 180.0)
    got = select_strikes_delta_band(
        wings, forward=forward, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(min_strikes_per_side=2),
    )
    assert got == (50.0, 60.0, 160.0, 170.0)


def test_boundary_exact_min_strikes_floor_when_band_thin() -> None:
    forward, maturity, vol, df = 100.0, 0.25, 0.20, 0.99
    strikes = (94.0, 96.0, 98.0, 100.0, 104.0, 130.0)
    got = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(min_strikes_per_side=2),
    )
    assert 104.0 in got and 130.0 in got
    assert 100.0 in got and 98.0 in got


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("forward", {"forward": 0.0}),
        ("forward", {"forward": float("nan")}),
        ("forward", {"forward": -100.0}),
        ("volatility", {"volatility": 0.0}),
        ("volatility", {"volatility": float("nan")}),
        ("volatility", {"volatility": float("inf")}),
        ("maturity_years", {"maturity_years": 0.0}),
        ("maturity_years", {"maturity_years": -0.25}),
        ("discount_factor", {"discount_factor": 0.0}),
        ("discount_factor", {"discount_factor": 1.5}),
        ("discount_factor", {"discount_factor": float("nan")}),
    ],
)
def test_unusable_pricing_input_raises_labeled_error(field: str, kwargs: dict) -> None:
    base = dict(
        forward=100.0, maturity_years=0.25, discount_factor=0.99, volatility=0.20,
    )
    base.update(kwargs)
    with pytest.raises(StrikeSelectionError) as exc:
        select_strikes_delta_band((90.0, 100.0, 110.0), selection=_cfg(), **base)
    assert exc.value.field == field


def test_kept_strikes_are_sorted_deduped_and_finite() -> None:
    forward = 100.0
    strikes = (100.0, 100.0, 98.0, 102.0, 98.0)
    got = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(),
    )
    assert list(got) == sorted(got)
    assert len(got) == len(set(got))
    assert all(math.isfinite(k) for k in got)


def test_reordering_input_does_not_change_band() -> None:
    ladder = build_delta_band_ladder()
    forward_order = select_strikes_delta_band(
        ladder.strikes, forward=ladder.forward, maturity_years=ladder.maturity_years,
        discount_factor=ladder.discount_factor, volatility=ladder.volatility, selection=_cfg(),
    )
    reverse_order = select_strikes_delta_band(
        tuple(reversed(ladder.strikes)), forward=ladder.forward,
        maturity_years=ladder.maturity_years, discount_factor=ladder.discount_factor,
        volatility=ladder.volatility, selection=_cfg(),
    )
    assert forward_order == reverse_order


def test_percent_of_spot_policy_is_distinct_and_unchanged() -> None:
    from algotrading.infra.universe import ChainSelection

    strikes = tuple(float(k) for k in range(80, 121, 2))
    pct = select_strikes(strikes, 100.0, ChainSelection(min_strikes_per_side=1))
    band = select_strikes_delta_band(
        strikes, forward=100.0, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(),
    )
    assert pct != band
    assert set(band).issubset(set(strikes))
    assert set(pct).issubset(set(strikes))


def test_discovery_delta_bound_widens_and_clamps() -> None:
    assert discovery_delta_bound(0.30) == pytest.approx(0.20)
    for economic in (0.30, 0.20, 0.10, 0.05, 0.01):
        disc = discovery_delta_bound(economic)
        assert 0.0 < disc < economic


def test_discovery_window_contains_30d_band_beyond_a_fixed_count() -> None:
    forward, maturity = 7400.0, 2.0
    fitted_vol = 0.15
    working_vol = 0.40
    economic = _cfg(delta_bound=0.30, min_strikes_per_side=1)
    ladder = tuple(float(k) for k in range(3000, 16001, 25))

    call_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=fitted_vol, target_call_nd1=0.30
    )
    put_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=fitted_vol, target_call_nd1=0.70
    )
    assert call_30 > forward + 16 * 25
    assert put_30 < forward - 16 * 25

    window = select_discovery_strikes(
        ladder, forward=forward, maturity_years=maturity,
        working_vol=working_vol, selection=economic,
    )
    assert min(window) <= put_30
    assert max(window) >= call_30

    band_over_full = select_strikes_delta_band(
        ladder, forward=forward, maturity_years=maturity, discount_factor=1.0,
        volatility=fitted_vol, selection=economic,
    )
    band_over_window = select_strikes_delta_band(
        window, forward=forward, maturity_years=maturity, discount_factor=1.0,
        volatility=fitted_vol, selection=economic,
    )
    assert band_over_window == band_over_full

    nearest_32 = tuple(sorted(sorted(ladder, key=lambda k: abs(k - forward))[:32]))
    band_over_count = select_strikes_delta_band(
        nearest_32, forward=forward, maturity_years=maturity, discount_factor=1.0,
        volatility=fitted_vol, selection=economic,
    )
    assert set(band_over_count) < set(band_over_full)


def test_discovery_window_widens_with_tenor() -> None:
    forward = 7400.0
    ladder = tuple(float(k) for k in range(3000, 16001, 25))
    near = select_discovery_strikes(
        ladder, forward=forward, maturity_years=0.05, working_vol=0.40,
        selection=_cfg(min_strikes_per_side=1),
    )
    far = select_discovery_strikes(
        ladder, forward=forward, maturity_years=3.0, working_vol=0.40,
        selection=_cfg(min_strikes_per_side=1),
    )
    assert max(far) > max(near)
    assert min(far) < min(near)


def test_discovery_window_short_tenor_stays_tight() -> None:
    forward, maturity = 7400.0, 10.0 / 365.0
    fitted_vol = 0.15
    ladder = tuple(float(k) for k in range(3000, 16001, 25))
    window = select_discovery_strikes(
        ladder, forward=forward, maturity_years=maturity, working_vol=0.40,
        selection=_cfg(min_strikes_per_side=1),
    )
    call_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=fitted_vol, target_call_nd1=0.30
    )
    put_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=fitted_vol, target_call_nd1=0.70
    )
    assert min(window) <= put_30 and max(window) >= call_30
    assert max(window) < forward * 1.20
    assert min(window) > forward * 0.80
    assert len(window) < len(ladder) // 4


def test_discovery_window_garbage_working_vol_raises_labeled() -> None:
    ladder = tuple(float(k) for k in range(7000, 7801, 25))
    for bad in (0.0, -0.20, float("nan"), float("inf")):
        with pytest.raises(StrikeSelectionError) as exc:
            select_discovery_strikes(
                ladder, forward=7400.0, maturity_years=1.0, working_vol=bad,
                selection=_cfg(min_strikes_per_side=1),
            )
        assert exc.value.field == "volatility"


def test_discovery_window_is_deterministic_and_reorder_invariant() -> None:
    forward = 7400.0
    ladder = tuple(float(k) for k in range(3000, 16001, 25))
    once = select_discovery_strikes(
        ladder, forward=forward, maturity_years=1.5, working_vol=0.40,
        selection=_cfg(min_strikes_per_side=1),
    )
    again = select_discovery_strikes(
        tuple(reversed(ladder)), forward=forward, maturity_years=1.5, working_vol=0.40,
        selection=_cfg(min_strikes_per_side=1),
    )
    assert once == again
