from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime

import pytest
from algotrading.core import source_ref, stamp
from algotrading.core.config import ScenarioConfig, StressSurfaceConfig
from algotrading.frontend.basket_scenarios import basket_stress, reconstruct_valuation
from algotrading.infra.contracts import Basket, BasketLeg, ProjectedOptionAnalytics
from algotrading.infra.pricing import UNIT_STRINGS, price
from algotrading.infra.risk import position_risk
from algotrading.infra.risk.scenarios import Scenario, shock_valuation
from algotrading.infra.risk.valuation import pricing_state_for

_TS = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)
_TRADE_DATE = date(2026, 6, 5)
_UND = "AAA"
_MULT = 10.0
_F = 100.0
_K = 100.0
_VOL = 0.2
_T = 1.0 / 12.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black76(forward: float, strike: float, sigma: float, t: float, right: str) -> float:
    sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * sqrt_t * sqrt_t) / sqrt_t
    d2 = d1 - sqrt_t
    if right == "C":
        return forward * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1)


def _prov():
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-frontend-0.1.0",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_TS,),
    )


def _row(
    *,
    delta_band: str = "atm",
    target_delta: float = 0.30,
    right: str = "C",
    tenor_label: str = "1m",
    price_override: float | None = None,
    surface_side: str = "combined",
    vol: float = _VOL,
) -> ProjectedOptionAnalytics:
    base_price = _black76(_F, _K, vol, _T, right) if price_override is None else price_override
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider="ibkr",
        underlying=_UND,
        tenor_label=tenor_label,
        maturity_years=_T,
        delta_band=delta_band,
        target_delta=target_delta,
        log_moneyness=math.log(_K / _F),
        strike=_K,
        forward_price=_F,
        implied_vol=vol,
        total_variance=vol * vol * _T,
        surface_side=surface_side,
        price=base_price,
        delta=0.5,
        gamma=0.02,
        vega=0.31,
        theta=-0.05,
        rho=0.04,
        dollar_delta=0.5 * _F * _MULT,
        dollar_gamma=0.02,
        dollar_vega=0.31,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS["dollar_gamma_one_pct"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=_prov(),
    )


def _config(steps: int = 5, *, rate_shocks: tuple[float, ...] = ()) -> ScenarioConfig:
    return ScenarioConfig(
        version="scn-basket-test",
        spot_shocks=(-0.05, 0.05),
        vol_shocks=(0.05,),
        rate_shocks=rate_shocks,
        stress_surface=StressSurfaceConfig(
            version="ss-basket-test",
            spot_shock_abs=0.5,
            vol_shock_abs=0.5,
            spot_steps=steps,
            vol_steps=steps,
        ),
    )


def _leg(
    side: str = "long",
    quantity: float = 2.0,
    *,
    delta_band: str = "atm",
    surface_side: str = "combined",
) -> BasketLeg:
    return BasketLeg(
        instrument_kind="option",
        side=side,
        quantity=quantity,
        underlying=_UND,
        tenor_label="1m",
        delta_band=delta_band,
        surface_side=surface_side,
    )


def _basket(*legs: BasketLeg) -> Basket:
    return Basket(basket_id="b", trade_date=_TRADE_DATE, underlying=_UND, legs=legs)


def test_reconstruct_reproduces_stored_price():
    row = _row()
    valuation = reconstruct_valuation(row, multiplier=_MULT, currency="USD")
    reprice = price(pricing_state_for(valuation)).price
    assert reprice == pytest.approx(row.price, abs=1e-9)
    assert valuation.discount_factor == pytest.approx(1.0, abs=1e-6)


def test_centre_cell_is_zero():
    row = _row()
    result = basket_stress(
        _basket(_leg()),
        analytics_rows=[row],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    ci = result.spot_axis.index(0.0)
    cj = result.vol_axis.index(0.0)
    assert result.pnl_grid[ci][cj] == pytest.approx(0.0, abs=1e-6)


def test_spot_shock_cell_matches_independent_black76():
    row = _row(right="C")
    quantity = 2.0
    valuation = reconstruct_valuation(row, multiplier=_MULT, currency="USD")
    discount_factor = valuation.discount_factor
    result = basket_stress(
        _basket(_leg("long", quantity)),
        analytics_rows=[row],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    i = result.spot_axis.index(0.25)
    j = result.vol_axis.index(0.0)
    scale = _MULT * quantity
    base = _black76(_F, _K, _VOL, _T, "C")
    shocked = _black76(_F * 1.25, _K, _VOL, _T, "C")
    expected = scale * discount_factor * (shocked - base)
    assert result.pnl_grid[i][j] == pytest.approx(expected, abs=1e-4)


def test_long_call_worst_case_is_the_spot_crash():
    row = _row(right="C")
    quantity = 2.0
    result = basket_stress(
        _basket(_leg("long", quantity)),
        analytics_rows=[row],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.worst_spot_shock == min(result.spot_axis)
    premium_loss = -_black76(_F, _K, _VOL, _T, "C") * _MULT * quantity
    assert result.worst_pnl == pytest.approx(premium_loss, abs=2.0)
    assert result.worst_pnl < 0.0


def test_atmp_band_reprices_as_a_put_not_a_call():
    # Regression: the ATM-put band carries target_delta == 0.0, so inferring the option right from
    # the target-delta sign mislabelled it a call. The right must come from the band label.
    put = reconstruct_valuation(_row(delta_band="atmp", right="P"), multiplier=_MULT, currency="USD")
    call = reconstruct_valuation(_row(delta_band="atm", right="C"), multiplier=_MULT, currency="USD")
    assert put.option_right == "P"
    assert call.option_right == "C"


def test_long_atm_straddle_surface_is_a_symmetric_v():
    # A long ATM straddle (call + put, same strike) must gain on BOTH a large up and a large down
    # move; the put-as-call bug made the down-spot wing a loss and pushed the worst case onto the
    # spot crash. Guard the V-shape at the spot extremes and an interior worst case.
    result = basket_stress(
        _basket(_leg("long", 1.0, delta_band="atm"), _leg("long", 1.0, delta_band="atmp")),
        analytics_rows=[_row(delta_band="atm", right="C"), _row(delta_band="atmp", right="P")],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    j = result.vol_axis.index(0.0)
    assert result.pnl_grid[0][j] > 0.0  # deep down-spot: put wing gains
    assert result.pnl_grid[-1][j] > 0.0  # deep up-spot: call wing gains
    assert result.worst_spot_shock not in (min(result.spot_axis), max(result.spot_axis))


def test_short_leg_flips_the_worst_case_to_an_up_move():
    short_call = _leg("short", -2.0)
    result = basket_stress(
        _basket(short_call),
        analytics_rows=[_row(right="C")],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.worst_spot_shock == max(result.spot_axis)
    assert result.worst_pnl < 0.0


def test_unresolved_leg_is_a_labelled_gap():
    result = basket_stress(
        _basket(_leg(delta_band="99d")),
        analytics_rows=[_row(delta_band="atm")],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.n_resolved == 0
    assert [g.reason for g in result.gaps] == ["no_analytics_row"]
    assert len(result.spot_axis) == 5
    assert all(cell == 0.0 for grid_row in result.pnl_grid for cell in grid_row)


def test_leg_reprices_off_its_requested_surface_side():
    call_vol = _VOL + 0.05
    combined = _row(delta_band="atm", right="C", surface_side="combined", vol=_VOL)
    call_wing = _row(delta_band="atm", right="C", surface_side="call", vol=call_vol)
    quantity = 2.0
    result = basket_stress(
        _basket(_leg("long", quantity, surface_side="call")),
        analytics_rows=[combined, call_wing],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.n_resolved == 1
    assert result.gaps == ()
    i = result.spot_axis.index(0.25)
    j = result.vol_axis.index(0.0)
    scale = _MULT * quantity
    base = _black76(_F, _K, call_vol, _T, "C")
    shocked = _black76(_F * 1.25, _K, call_vol, _T, "C")
    expected = scale * (shocked - base)
    assert result.pnl_grid[i][j] == pytest.approx(expected, abs=1e-4)
    combined_base = _black76(_F, _K, _VOL, _T, "C")
    combined_shocked = _black76(_F * 1.25, _K, _VOL, _T, "C")
    assert expected != pytest.approx(scale * (combined_shocked - combined_base), abs=1e-4)


def test_requested_wing_with_no_curve_is_a_labelled_gap():
    result = basket_stress(
        _basket(_leg(surface_side="call")),
        analytics_rows=[_row(surface_side="combined")],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.n_resolved == 0
    assert [g.reason for g in result.gaps] == ["surface_side_unavailable"]


def test_missing_instrument_master_is_a_labelled_gap():
    result = basket_stress(
        _basket(_leg()),
        analytics_rows=[_row()],
        multiplier=None,
        currency=None,
        spot_by_underlying={},
        config=_config(),
    )
    assert result.n_resolved == 0
    assert [g.reason for g in result.gaps] == ["no_instrument_master"]


def test_stock_leg_linear_overlay():
    stock = BasketLeg(instrument_kind="stock", side="long", quantity=10.0, underlying=_UND)
    result = basket_stress(
        _basket(stock),
        analytics_rows=[],
        multiplier=None,
        currency=None,
        spot_by_underlying={_UND: 50.0},
        config=_config(),
    )
    i = result.spot_axis.index(0.25)
    expected = 10.0 * 50.0 * 0.25
    assert all(result.pnl_grid[i][j] == pytest.approx(expected, abs=1e-9) for j in range(5))
    assert result.n_resolved == 1


def test_no_rate_shocks_means_no_rate_sweep():
    # Backward-compatible: an unconfigured rate axis yields an empty sweep.
    result = basket_stress(
        _basket(_leg()),
        analytics_rows=[_row()],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.rate_sweep == ()


def test_rate_sweep_matches_independent_reprice():
    rate_shocks = (-0.0025, 0.0, 0.0025)
    row = _row(right="C")
    quantity = 2.0
    result = basket_stress(
        _basket(_leg("long", quantity)),
        analytics_rows=[row],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(rate_shocks=rate_shocks),
    )

    # One cell per configured rate shock, sorted ascending, each labelled with its shock.
    assert tuple(cell.rate_shock for cell in result.rate_sweep) == rate_shocks
    assert all(cell.n_legs == 1 for cell in result.rate_sweep)
    assert all(cell.scenario_id == f"rate_{cell.rate_shock:+.4f}" for cell in result.rate_sweep)

    # Independently rebuild the single leg and full-reprice each rate scenario, clamping the
    # shocked discount factor to (0, 1] exactly as the basket engine does.
    valuation = reconstruct_valuation(row, multiplier=_MULT, currency="USD")
    line = position_risk(portfolio_id="basket-stress", quantity=quantity, valuation=valuation)
    for cell in result.rate_sweep:
        scenario = Scenario(cell.scenario_id, "rate", 0.0, 0.0, 0.0, cell.rate_shock)
        shocked = shock_valuation(line.valuation, scenario)
        if shocked.discount_factor > 1.0:
            shocked = dataclasses.replace(shocked, discount_factor=1.0)
        shocked_price = price(pricing_state_for(shocked)).price
        expected = (shocked_price - line.greeks.price) * line.scale
        assert cell.scenario_pnl == pytest.approx(expected, abs=1e-9)

    # The zero-rate-shock cell is a no-op; a positive shock raises the rate, lowering the
    # call's discount factor and moving the book. The negative shock floors at rate 0 (DF=1),
    # since the rate-free reconstruction starts at implied rate ~0.
    by_shock = {c.rate_shock: c.scenario_pnl for c in result.rate_sweep}
    assert by_shock[0.0] == pytest.approx(0.0, abs=1e-9)
    assert by_shock[0.0025] != pytest.approx(0.0, abs=1e-9)
    assert by_shock[-0.0025] == pytest.approx(0.0, abs=1e-9)
