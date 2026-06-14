"""Multi-leg basket risk: book-additive summation of analytics dollar Greeks (WS 2A).

The independent oracle is the hand-written per-leg dollar Greeks in each test (chosen here,
never read from the code under test): the basket aggregate must equal the hand sum
``Σ signed_quantity · leg.dollar_<greek>``. This is the falsifiable form of "priced from the
Tab-1 analytics, never a recompute". Per ``tasks/PHASE2-prep-ready-on-commit.md`` the sum is in
the analytics convention (per-1% / per-365) carried on the rows — the module never touches the
legacy per-$1 ``PositionRisk`` Greeks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from algotrading.core import source_ref, stamp
from algotrading.infra.contracts import Basket, BasketLeg, ProjectedOptionAnalytics
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.risk.multileg import basket_risk

_TS = datetime(2026, 6, 5, 21, 0, tzinfo=UTC)
_TRADE_DATE = date(2026, 6, 5)
_UND = "AAA"


def _prov():
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_TS,),
    )


def _row(
    *,
    delta_band: str,
    dollar_delta: float,
    dollar_gamma: float,
    dollar_vega: float,
    price: float,
    dollar_theta: float | None = -0.001,
    dollar_rho: float | None = 0.002,
    tenor_label: str = "1m",
    underlying: str = _UND,
    provider: str = "ibkr",
    surface_side: str = "combined",
) -> ProjectedOptionAnalytics:
    """One analytics grid cell with hand-chosen dollar Greeks (the oracle inputs)."""
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider=provider,
        underlying=underlying,
        tenor_label=tenor_label,
        maturity_years=1.0 / 12.0,
        delta_band=delta_band,
        surface_side=surface_side,
        target_delta=0.30 if delta_band.endswith("c") else -0.30,
        log_moneyness=0.0,
        strike=100.0,
        forward_price=100.0,
        implied_vol=0.2,
        total_variance=0.2 * 0.2 / 12.0,
        price=price,
        delta=0.5,
        gamma=0.02,
        vega=0.31,
        theta=-0.05,
        rho=0.04,
        dollar_delta=dollar_delta,
        dollar_gamma=dollar_gamma,
        dollar_vega=dollar_vega,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS["dollar_gamma_one_pct"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=_prov(),
        dollar_theta=dollar_theta,
        dollar_rho=dollar_rho,
        dollar_theta_unit=None if dollar_theta is None else UNIT_STRINGS["dollar_theta_365"],
        dollar_rho_unit=None if dollar_rho is None else UNIT_STRINGS["dollar_rho"],
    )


def _basket(*legs: BasketLeg, basket_id: str = "b1") -> Basket:
    return Basket(basket_id=basket_id, trade_date=_TRADE_DATE, underlying=_UND, legs=legs)


def test_basket_dollar_greeks_equal_sum_of_leg_analytics() -> None:
    # Independent oracle: a risk-reversal — long 1 of the 30Δ call cell, short 1 of the 30Δ put.
    # Per-leg dollar Greeks are hand-chosen below; the basket aggregate must equal the hand sum
    # Σ signed_quantity · row.dollar_<greek>.
    call = _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.5,
                price=4.0, dollar_theta=-0.010, dollar_rho=0.030)
    put = _row(delta_band="30dp", dollar_delta=-8.0, dollar_gamma=1.5, dollar_vega=0.4,
               price=3.0, dollar_theta=-0.008, dollar_rho=-0.020)
    basket = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc"),
        BasketLeg("option", "short", -1.0, _UND, tenor_label="1m", delta_band="30dp"),
    )

    result = basket_risk(basket, analytics_rows=[call, put], spot_by_underlying={})

    # Hand sums (q_call=+1, q_put=-1):
    #   delta = 1*10  + (-1)*(-8) = 18.0
    #   gamma = 1*2.0 + (-1)*1.5  = 0.5
    #   vega  = 1*0.5 + (-1)*0.4  = 0.1
    #   theta = 1*(-0.010) + (-1)*(-0.008) = -0.002
    #   rho   = 1*0.030 + (-1)*(-0.020)    = 0.050
    #   price = 1*4.0 + (-1)*3.0 = 1.0
    assert result.dollar_delta == pytest.approx(18.0)
    assert result.dollar_gamma == pytest.approx(0.5)
    assert result.dollar_vega == pytest.approx(0.1)
    assert result.dollar_theta == pytest.approx(-0.002)
    assert result.dollar_rho == pytest.approx(0.05)
    assert result.price == pytest.approx(1.0)
    assert result.gaps == ()
    # Unit strings carried through from the rows, not invented.
    assert result.dollar_delta_unit == UNIT_STRINGS["dollar_delta"]
    assert result.dollar_gamma_unit == UNIT_STRINGS["dollar_gamma_one_pct"]


def test_per_leg_contribution_equals_signed_quantity_times_row() -> None:
    # The line-level proof 2C attributes off: each leg's contribution == q · row.dollar_<greek>.
    call = _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.5, price=4.0)
    basket = _basket(BasketLeg("option", "long", 3.0, _UND, tenor_label="1m", delta_band="30dc"))
    result = basket_risk(basket, analytics_rows=[call], spot_by_underlying={})
    (leg,) = result.legs
    assert leg.resolved is True
    assert leg.dollar_delta == pytest.approx(30.0)  # 3 * 10
    assert leg.dollar_gamma == pytest.approx(6.0)  # 3 * 2.0


def test_basket_risk_is_reordering_invariant() -> None:
    # Order-free summation (math.fsum): shuffling the legs leaves the aggregate identical.
    call = _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.5, price=4.0)
    put = _row(delta_band="30dp", dollar_delta=-8.0, dollar_gamma=1.5, dollar_vega=0.4, price=3.0)
    leg_c = BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc")
    leg_p = BasketLeg("option", "short", -1.0, _UND, tenor_label="1m", delta_band="30dp")
    rows = [call, put]
    forward = basket_risk(_basket(leg_c, leg_p), analytics_rows=rows, spot_by_underlying={})
    reverse = basket_risk(_basket(leg_p, leg_c), analytics_rows=rows, spot_by_underlying={})
    for greek in ("dollar_delta", "dollar_gamma", "dollar_vega", "dollar_theta", "dollar_rho", "price"):
        assert getattr(forward, greek) == getattr(reverse, greek)


def test_stock_leg_dollar_delta_is_qty_times_spot_others_zero() -> None:
    # A share has a linear spot delta and no option Greeks.
    basket = _basket(BasketLeg("stock", "long", 10.0, _UND))
    result = basket_risk(basket, analytics_rows=[], spot_by_underlying={_UND: 123.5})
    assert result.dollar_delta == pytest.approx(1235.0)  # 10 * 123.5
    assert result.dollar_gamma == 0.0
    assert result.dollar_vega == 0.0
    assert result.dollar_theta == 0.0
    assert result.dollar_rho == 0.0
    (leg,) = result.legs
    assert leg.dollar_delta_unit == UNIT_STRINGS["dollar_delta"]
    assert result.gaps == ()


def test_short_stock_leg_has_negative_dollar_delta() -> None:
    basket = _basket(BasketLeg("stock", "short", -4.0, _UND))
    result = basket_risk(basket, analytics_rows=[], spot_by_underlying={_UND: 50.0})
    assert result.dollar_delta == pytest.approx(-200.0)  # -4 * 50


def test_unpriced_leg_is_labeled_gap_not_zero() -> None:
    # A leg whose cell has no analytics row is a labelled gap carrying the missing coordinate —
    # never a silent zero, never a bare NaN; the aggregate does not absorb it.
    present = _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.5, price=4.0)
    basket = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc"),
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="10dp"),  # not seeded
    )
    result = basket_risk(basket, analytics_rows=[present], spot_by_underlying={})
    assert result.dollar_delta == pytest.approx(10.0)  # only the resolved leg, not 10+0
    gap_legs = [lr for lr in result.legs if not lr.resolved]
    assert len(gap_legs) == 1
    assert gap_legs[0].gap_reason == "no_analytics_row"
    assert gap_legs[0].dollar_delta is None  # not 0.0
    assert result.gaps == (
        type(result.gaps[0])(_UND, "1m", "10dp", "no_analytics_row"),
    )


def test_no_spot_for_stock_leg_is_labeled_gap() -> None:
    basket = _basket(BasketLeg("stock", "long", 5.0, _UND))
    result = basket_risk(basket, analytics_rows=[], spot_by_underlying={})  # no spot
    (leg,) = result.legs
    assert leg.resolved is False
    assert leg.gap_reason == "no_spot_for_stock_leg"
    assert result.dollar_delta == 0.0  # empty sum, the gap is reported separately
    assert result.gaps[0].reason == "no_spot_for_stock_leg"


def test_basket_theta_none_when_a_leg_row_theta_is_none() -> None:
    # Additive-nullable: a row written before P0.2 carries dollar_theta=None. The basket theta is
    # then unavailable (None) with a labelled gap — never silently 0, never NaN. delta still sums.
    with_theta = _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.5,
                      price=4.0, dollar_theta=-0.01, dollar_rho=0.03)
    no_theta = _row(delta_band="30dp", dollar_delta=-8.0, dollar_gamma=1.5, dollar_vega=0.4,
                    price=3.0, dollar_theta=None, dollar_rho=0.02)
    basket = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc"),
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dp"),
    )
    result = basket_risk(basket, analytics_rows=[with_theta, no_theta], spot_by_underlying={})
    assert result.dollar_delta == pytest.approx(2.0)  # 10 + (-8) still sums
    assert result.dollar_theta is None  # unavailable, labelled
    assert any(g.reason == "theta_unavailable" for g in result.gaps)
    assert result.dollar_rho == pytest.approx(0.05)  # rho present on both: 0.03 + 0.02


def test_duplicate_cell_across_two_legs_both_contribute() -> None:
    # Two legs referencing the same cell both contribute (no netting — distinct legs stay visible).
    call = _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.5, price=4.0)
    basket = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc"),
        BasketLeg("option", "long", 2.0, _UND, tenor_label="1m", delta_band="30dc"),
    )
    result = basket_risk(basket, analytics_rows=[call], spot_by_underlying={})
    assert len(result.legs) == 2
    assert result.dollar_delta == pytest.approx(30.0)  # (1+2) * 10


def test_provider_ambiguous_cell_is_labeled_gap_not_silent_pick() -> None:
    # A cell seeded by two providers in the read scope is ambiguous: a leg on it is a labelled
    # gap, never an arbitrary silent pick of one provider.
    ibkr = _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.5,
                price=4.0, provider="ibkr")
    saxo = _row(delta_band="30dc", dollar_delta=99.0, dollar_gamma=9.0, dollar_vega=9.0,
                price=9.0, provider="saxo")
    basket = _basket(BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc"))
    result = basket_risk(basket, analytics_rows=[ibkr, saxo], spot_by_underlying={})
    (leg,) = result.legs
    assert leg.resolved is False
    assert leg.gap_reason == "provider_ambiguous"
    assert result.dollar_delta == 0.0  # neither provider's number is used


def test_empty_basket_is_labeled_empty_not_a_crash() -> None:
    result = basket_risk(_basket(), analytics_rows=[], spot_by_underlying={})
    assert result.legs == ()
    assert result.gaps == ()
    assert result.dollar_delta == 0.0
    assert result.dollar_gamma == 0.0
    assert result.price == 0.0


def test_single_leg_basket() -> None:
    call = _row(delta_band="atm", dollar_delta=5.0, dollar_gamma=1.0, dollar_vega=0.2, price=2.0)
    basket = _basket(BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="atm"))
    result = basket_risk(basket, analytics_rows=[call], spot_by_underlying={})
    assert result.dollar_delta == pytest.approx(5.0)
    assert len(result.legs) == 1


# --- per-side surface routing (ADR 0048) -------------------------------------------------------
# One cell now carries up to three rows (put / call / combined), differing only by which fitted
# surface supplied the IV. The oracle for each test is the hand-chosen per-side dollar Greeks: a
# leg must read off the surface it names, never mutualise onto the combined row.


def _sided_cell() -> list[ProjectedOptionAnalytics]:
    """The 30dc cell as three surface sides, each with a distinct, hand-chosen dollar_vega."""
    return [
        _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.50,
             price=4.00, surface_side="combined"),
        _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.55,
             price=4.20, surface_side="put"),
        _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.45,
             price=3.80, surface_side="call"),
    ]


def test_leg_routes_to_its_requested_surface_side() -> None:
    # A leg that names the call wing reads the call row's vega/price; the put wing reads the put
    # row's. Independent oracle: the per-side dollar_vega/price hand-chosen in _sided_cell.
    rows = _sided_cell()
    call_leg = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc",
                  surface_side="call")
    )
    put_leg = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc",
                  surface_side="put")
    )
    call_res = basket_risk(call_leg, analytics_rows=rows, spot_by_underlying={})
    put_res = basket_risk(put_leg, analytics_rows=rows, spot_by_underlying={})
    assert call_res.dollar_vega == pytest.approx(0.45)
    assert call_res.price == pytest.approx(3.80)
    assert put_res.dollar_vega == pytest.approx(0.55)
    assert put_res.price == pytest.approx(4.20)
    assert call_res.gaps == ()
    assert put_res.gaps == ()


def test_default_leg_reads_combined_even_when_wings_present() -> None:
    # The default (unspecified) leg sums only the combined row — so the additive put/call rows
    # never double-count or shift the legacy basket number (ADR 0048: combined is the reference).
    rows = _sided_cell()
    basket = _basket(BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc"))
    result = basket_risk(basket, analytics_rows=rows, spot_by_underlying={})
    assert result.dollar_vega == pytest.approx(0.50)  # the combined row only
    assert result.price == pytest.approx(4.00)
    assert len(result.legs) == 1
    assert result.gaps == ()


def test_straddle_wings_price_off_their_own_surfaces() -> None:
    # The motivating case: an S1 straddle — long the ATM call wing + long the ATM put wing — must
    # price each leg off its own surface, not one mutualised IV. Oracle: vega = call-wing vega +
    # put-wing vega = 0.45 + 0.55 = 1.0 (would be 0.50+0.50=1.0 only by coincidence if both read
    # combined; the per-side prices below make the distinction unambiguous).
    rows = _sided_cell()
    straddle = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc",
                  surface_side="call"),
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc",
                  surface_side="put"),
    )
    result = basket_risk(straddle, analytics_rows=rows, spot_by_underlying={})
    assert result.price == pytest.approx(3.80 + 4.20)  # call wing + put wing, not 4.00 + 4.00
    assert result.dollar_vega == pytest.approx(0.45 + 0.55)
    assert result.gaps == ()


def test_requested_wing_with_no_curve_is_labeled_gap_not_combined_fallback() -> None:
    # The cell has combined + put rows but no call row (the call wing had too few points to fit).
    # A leg that asks for the call wing is a labelled gap — never a silent fall back to combined,
    # which would re-mutualise the very IV the wing selection exists to separate.
    rows = [
        _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.50,
             price=4.0, surface_side="combined"),
        _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.55,
             price=4.2, surface_side="put"),
    ]
    basket = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc",
                  surface_side="call")
    )
    result = basket_risk(basket, analytics_rows=rows, spot_by_underlying={})
    (leg,) = result.legs
    assert leg.resolved is False
    assert leg.gap_reason == "surface_side_unavailable"
    assert leg.dollar_vega is None  # not 0.50 (the combined row), not 0.0
    assert result.dollar_vega == 0.0  # empty sum; the gap is reported, never absorbed
    assert result.gaps == (
        type(result.gaps[0])(_UND, "1m", "30dc", "surface_side_unavailable"),
    )


def test_provider_ambiguity_is_isolated_per_surface_side() -> None:
    # Two providers seed the *combined* side of the cell (ambiguous), but only one seeds the call
    # side. A combined leg is a gap; a call leg on the cleanly-single-provider call wing resolves.
    rows = [
        _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.50,
             price=4.0, surface_side="combined", provider="ibkr"),
        _row(delta_band="30dc", dollar_delta=99.0, dollar_gamma=9.0, dollar_vega=9.00,
             price=9.0, surface_side="combined", provider="saxo"),
        _row(delta_band="30dc", dollar_delta=10.0, dollar_gamma=2.0, dollar_vega=0.45,
             price=3.8, surface_side="call", provider="ibkr"),
    ]
    combined_leg = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc")
    )
    call_leg = _basket(
        BasketLeg("option", "long", 1.0, _UND, tenor_label="1m", delta_band="30dc",
                  surface_side="call")
    )
    combined_res = basket_risk(combined_leg, analytics_rows=rows, spot_by_underlying={})
    call_res = basket_risk(call_leg, analytics_rows=rows, spot_by_underlying={})
    assert combined_res.legs[0].gap_reason == "provider_ambiguous"
    assert combined_res.dollar_vega == 0.0
    assert call_res.legs[0].resolved is True  # the call side is unambiguous
    assert call_res.dollar_vega == pytest.approx(0.45)
