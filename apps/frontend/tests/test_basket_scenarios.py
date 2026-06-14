"""Unit tests for the on-demand basket stress surface (WS 2B interactive).

Expected values are derived independently of the engine: the option price and every shocked
reprice are computed here from a hand-written Black-76 (forward-based, the carry-0 / spot==forward
state the projection uses), so a wiring bug (wrong scale, dropped discount factor, sign flip)
fails rather than being masked by reusing the code under test.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.core import source_ref, stamp
from algotrading.core.config import ScenarioConfig, StressSurfaceConfig
from algotrading.frontend.basket_scenarios import basket_stress, reconstruct_valuation
from algotrading.infra.contracts import Basket, BasketLeg, ProjectedOptionAnalytics
from algotrading.infra.pricing import UNIT_STRINGS, price
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
    """Standard normal CDF via the error function — the test's own, not the engine's."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black76(forward: float, strike: float, sigma: float, t: float, right: str) -> float:
    """Undiscounted Black-76 value of a European option (DF applied separately)."""
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
    """One grid cell whose ``price`` is the independent Black-76 value (DF backs out to 1.0).

    ``vol`` is the surface side's IV; ``price`` is the Black-76 value at that IV, so a per-side
    row reprices off its own wing exactly as the projection emits it (ADR 0048).
    """
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


def _config(steps: int = 5) -> ScenarioConfig:
    return ScenarioConfig(
        version="scn-basket-test",
        spot_shocks=(-0.05, 0.05),
        vol_shocks=(0.05,),
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
    """The DF back-out makes the base reprice reproduce the row's stored price exactly."""
    row = _row()
    valuation = reconstruct_valuation(row, multiplier=_MULT, currency="USD")
    reprice = price(pricing_state_for(valuation)).price
    assert reprice == pytest.approx(row.price, abs=1e-9)
    # The stored price was an undiscounted Black-76 value, so the recovered DF is ~1.
    assert valuation.discount_factor == pytest.approx(1.0, abs=1e-6)


def test_centre_cell_is_zero():
    """The (0 spot, 0 vol) centre cell is ~0 PnL by construction."""
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
    """A pure-spot-shock cell equals the independent Black-76 reprice difference x scale x DF."""
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
    i = result.spot_axis.index(0.25)  # +25% spot
    j = result.vol_axis.index(0.0)  # no vol shock
    scale = _MULT * quantity
    base = _black76(_F, _K, _VOL, _T, "C")
    shocked = _black76(_F * 1.25, _K, _VOL, _T, "C")
    expected = scale * discount_factor * (shocked - base)
    assert result.pnl_grid[i][j] == pytest.approx(expected, abs=1e-4)


def test_long_call_worst_case_is_the_spot_crash():
    """A long call's largest loss is the deepest spot crash; it loses ~the full premium x scale."""
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
    # At -50% spot a 1m ATM call is nearly worthless: loss ~ premium x scale.
    premium_loss = -_black76(_F, _K, _VOL, _T, "C") * _MULT * quantity
    assert result.worst_pnl == pytest.approx(premium_loss, abs=2.0)
    assert result.worst_pnl < 0.0


def test_short_leg_flips_the_worst_case_to_an_up_move():
    """A short call loses when spot rallies, so its worst case is the up-move, not the crash."""
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
    """A leg with no matching grid cell is a labelled gap; the surface still builds."""
    result = basket_stress(
        _basket(_leg(delta_band="99d")),  # not in the rows
        analytics_rows=[_row(delta_band="atm")],
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.n_resolved == 0
    assert [g.reason for g in result.gaps] == ["no_analytics_row"]
    # An empty book still reprices to a valid (flat-zero) surface over the config axes.
    assert len(result.spot_axis) == 5
    assert all(cell == 0.0 for grid_row in result.pnl_grid for cell in grid_row)


def test_leg_reprices_off_its_requested_surface_side():
    """A call-wing leg reprices off the call surface's IV, not the combined IV (ADR 0048).

    The cell carries a combined row at _VOL and a call row at a higher IV; the +spot-shock cell
    of a call-wing leg must equal the independent Black-76 reprice at the *call* IV.
    """
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
    i = result.spot_axis.index(0.25)  # +25% spot
    j = result.vol_axis.index(0.0)  # no vol shock
    scale = _MULT * quantity  # DF ~ 1: the stored price is the undiscounted Black-76 at call_vol
    base = _black76(_F, _K, call_vol, _T, "C")
    shocked = _black76(_F * 1.25, _K, call_vol, _T, "C")
    expected = scale * (shocked - base)
    assert result.pnl_grid[i][j] == pytest.approx(expected, abs=1e-4)
    # And it is genuinely the call IV, not the combined IV: the combined-IV reprice differs.
    combined_base = _black76(_F, _K, _VOL, _T, "C")
    combined_shocked = _black76(_F * 1.25, _K, _VOL, _T, "C")
    assert expected != pytest.approx(scale * (combined_shocked - combined_base), abs=1e-4)


def test_requested_wing_with_no_curve_is_a_labelled_gap():
    """A leg asking for a wing the cell has no row for is a gap — never a silent combined reprice."""
    result = basket_stress(
        _basket(_leg(surface_side="call")),
        analytics_rows=[_row(surface_side="combined")],  # only the combined side exists
        multiplier=_MULT,
        currency="USD",
        spot_by_underlying={},
        config=_config(),
    )
    assert result.n_resolved == 0
    assert [g.reason for g in result.gaps] == ["surface_side_unavailable"]


def test_missing_instrument_master_is_a_labelled_gap():
    """No multiplier/currency (no instrument master) → every option leg is a labelled gap."""
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
    """A stock leg adds a vol-independent linear PnL (qty x spot x spot_shock) to every cell."""
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
    # vol-independent: same across every vol column at this spot shock.
    expected = 10.0 * 50.0 * 0.25
    assert all(result.pnl_grid[i][j] == pytest.approx(expected, abs=1e-9) for j in range(5))
    assert result.n_resolved == 1
