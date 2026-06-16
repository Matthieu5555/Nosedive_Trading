from __future__ import annotations

import dataclasses
import inspect
import math
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra import pricing
from algotrading.infra.contracts import InstrumentKey, PricingResult, table_for_contract, validate
from algotrading.infra.pricing import (
    PRICER_VERSION,
    PriceGreeks,
    PricingError,
    PricingState,
    bjerksund_stensland_price,
    dollar_greeks,
    price,
    price_american,
    price_european,
    pricing_result,
)
from algotrading.infra.pricing.state import from_forward, from_spot
from fixtures.synthetic import black_call, black_put

REF_F, REF_K, REF_T, REF_VOL, REF_DF = 100.0, 100.0, 0.25, 0.20, 0.99
REF_RATE = -math.log(REF_DF) / REF_T


def ref_state(option_right: str, exercise_style: str = "european") -> PricingState:
    return from_forward(
        forward=REF_F,
        strike=REF_K,
        maturity_years=REF_T,
        volatility=REF_VOL,
        discount_factor=REF_DF,
        option_right=option_right,
        exercise_style=exercise_style,
    )


@pytest.mark.parametrize("right", ["C", "P"])
def test_european_price_matches_independent_black76(right: str) -> None:
    oracle = (black_call if right == "C" else black_put)(REF_F, REF_K, REF_T, REF_VOL, REF_DF)
    got = price_european(ref_state(right)).price
    assert got == pytest.approx(oracle, rel=1e-10)
    assert got == pytest.approx(3.947884, abs=1e-5)


def test_european_matches_hull_textbook_example() -> None:
    spot, strike, rate, vol, mat = 42.0, 40.0, 0.10, 0.20, 0.5
    df = math.exp(-rate * mat)
    call = price_european(
        from_spot(
            spot=spot, strike=strike, maturity_years=mat, volatility=vol,
            discount_factor=df, option_right="C", carry=rate,
        )
    ).price
    put = price_european(
        from_spot(
            spot=spot, strike=strike, maturity_years=mat, volatility=vol,
            discount_factor=df, option_right="P", carry=rate,
        )
    ).price
    assert call == pytest.approx(4.76, abs=0.01)
    assert put == pytest.approx(0.81, abs=0.01)
    forward = spot * math.exp(rate * mat)
    assert call - put == pytest.approx(df * (forward - strike), rel=1e-10)


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize(
    ("rate", "div_yield"),
    [(0.05, 0.0), (0.05, 0.03)],
)
def test_black76_and_black_scholes_agree_under_the_documented_carry(
    right: str, rate: float, div_yield: float
) -> None:
    spot, strike, vol, mat = 100.0, 95.0, 0.20, 0.5
    carry = rate - div_yield
    forward = spot * math.exp(carry * mat)
    df = math.exp(-rate * mat)
    black_scholes = price_european(
        from_spot(
            spot=spot, strike=strike, maturity_years=mat, volatility=vol,
            discount_factor=df, option_right=right, carry=carry,
        )
    ).price
    black76 = price_european(
        from_forward(
            forward=forward, strike=strike, maturity_years=mat, volatility=vol,
            discount_factor=df, option_right=right,
        )
    ).price
    oracle = (black_call if right == "C" else black_put)(forward, strike, mat, vol, df)
    assert black_scholes == pytest.approx(oracle, rel=1e-10)
    assert black76 == pytest.approx(oracle, rel=1e-10)
    assert black_scholes == pytest.approx(black76, rel=1e-12)


@pytest.mark.parametrize("right", ["C", "P"])
def test_zero_vol_gives_discounted_intrinsic(right: str) -> None:
    state = from_forward(
        forward=110.0, strike=100.0, maturity_years=REF_T, volatility=0.0,
        discount_factor=REF_DF, option_right=right,
    )
    expected = REF_DF * (max(110.0 - 100.0, 0.0) if right == "C" else max(100.0 - 110.0, 0.0))
    greeks = price_european(state)
    assert greeks.price == pytest.approx(expected, abs=1e-12)
    assert greeks.gamma == pytest.approx(0.0, abs=1e-12)
    assert greeks.vega == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("right", ["C", "P"])
def test_zero_maturity_gives_intrinsic(right: str) -> None:
    state = from_forward(
        forward=107.0, strike=100.0, maturity_years=0.0, volatility=REF_VOL,
        discount_factor=1.0, option_right=right,
    )
    expected = max(107.0 - 100.0, 0.0) if right == "C" else max(100.0 - 107.0, 0.0)
    assert price_european(state).price == pytest.approx(expected, abs=1e-12)


def test_deep_itm_call_approaches_discounted_forward_minus_strike() -> None:
    tiny_strike = 1e-8
    call = price_european(
        from_forward(forward=REF_F, strike=tiny_strike, maturity_years=REF_T,
                     volatility=REF_VOL, discount_factor=REF_DF, option_right="C")
    ).price
    put = price_european(
        from_forward(forward=REF_F, strike=tiny_strike, maturity_years=REF_T,
                     volatility=REF_VOL, discount_factor=REF_DF, option_right="P")
    ).price
    assert call == pytest.approx(REF_DF * (REF_F - tiny_strike), rel=1e-6)
    assert put == pytest.approx(0.0, abs=1e-6)


def test_very_high_vol_call_saturates_at_discounted_forward() -> None:
    call = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=REF_T,
                     volatility=20.0, discount_factor=REF_DF, option_right="C")
    ).price
    assert call == pytest.approx(REF_DF * REF_F, rel=1e-3)
    assert call < REF_DF * REF_F


def test_volatility_is_decimal_not_percent() -> None:
    assert price_european(ref_state("C")).price == pytest.approx(3.947884, abs=1e-5)
    saturated = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=REF_T,
                     volatility=20.0, discount_factor=REF_DF, option_right="C")
    ).price
    near_intrinsic = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=REF_T,
                     volatility=0.002, discount_factor=REF_DF, option_right="C")
    ).price
    assert near_intrinsic < 0.1 < 3.947884 < saturated


def test_maturity_is_years_not_days() -> None:
    quarter = price_european(ref_state("C")).price
    one_day_if_misread = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=0.25 / 365.0,
                     volatility=REF_VOL, discount_factor=REF_DF, option_right="C")
    ).price
    one_year = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=1.0,
                     volatility=REF_VOL, discount_factor=REF_DF, option_right="C")
    ).price
    assert one_day_if_misread < 0.5 < quarter < one_year


def _fd_forward_first(fn: Callable[[float], float], x: float, h: float) -> float:
    return (fn(x + h) - fn(x - h)) / (2.0 * h)


def _fixture_price(
    right: str, *, f: float, t: float, vol: float, df: float, k: float = REF_K
) -> float:
    return (black_call if right == "C" else black_put)(f, k, t, vol, df)


@pytest.mark.parametrize("right", ["C", "P"])
def test_delta_matches_fd_of_independent_price(right: str) -> None:
    fd = _fd_forward_first(
        lambda f: _fixture_price(right, f=f, t=REF_T, vol=REF_VOL, df=REF_DF), REF_F, 1e-4
    )
    assert price_european(ref_state(right)).delta == pytest.approx(fd, abs=1e-6)


@pytest.mark.parametrize("right", ["C", "P"])
def test_gamma_matches_fd_second_derivative(right: str) -> None:
    h = 1e-2
    base = lambda f: _fixture_price(right, f=f, t=REF_T, vol=REF_VOL, df=REF_DF)  # noqa: E731
    fd = (base(REF_F + h) - 2.0 * base(REF_F) + base(REF_F - h)) / (h * h)
    assert price_european(ref_state(right)).gamma == pytest.approx(fd, abs=1e-5)
    assert price_european(ref_state(right)).gamma > 0.0


@pytest.mark.parametrize("right", ["C", "P"])
def test_vega_matches_fd_in_vol(right: str) -> None:
    fd = _fd_forward_first(
        lambda v: _fixture_price(right, f=REF_F, t=REF_T, vol=v, df=REF_DF), REF_VOL, 1e-5
    )
    assert price_european(ref_state(right)).vega == pytest.approx(fd, rel=1e-4)


@pytest.mark.parametrize("right", ["C", "P"])
def test_theta_matches_spot_fixed_fd(right: str) -> None:
    def px(t: float) -> float:
        return _fixture_price(right, f=REF_F, t=t, vol=REF_VOL, df=math.exp(-REF_RATE * t))

    fd = -_fd_forward_first(px, REF_T, 1e-4)
    assert price_european(ref_state(right)).theta == pytest.approx(fd, abs=1e-3)
    assert price_european(ref_state(right)).theta < 0.0


@pytest.mark.parametrize("right", ["C", "P"])
def test_rho_matches_fd_in_rate_forward_fixed(right: str) -> None:
    def px(rate: float) -> float:
        return _fixture_price(right, f=REF_F, t=REF_T, vol=REF_VOL, df=math.exp(-rate * REF_T))

    fd = _fd_forward_first(px, REF_RATE, 1e-6)
    assert price_european(ref_state(right)).rho == pytest.approx(fd, abs=1e-4)


_SECOND_ORDER_POINTS = [
    (100.0, 0.20, 0.50, 0.03, 0.00),
    (110.0, 0.25, 0.75, 0.05, 0.05),
    (90.0, 0.15, 1.50, 0.04, 0.01),
    (100.0, 0.30, 0.10, 0.02, 0.00),
]


def _spot_state(spot: float, vol: float, t: float, right: str, rate: float, carry: float) -> PricingState:
    return from_spot(
        spot=spot, strike=100.0, maturity_years=t, volatility=vol,
        discount_factor=math.exp(-rate * t), option_right=right, carry=carry,
    )


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize(("spot", "vol", "t", "rate", "carry"), _SECOND_ORDER_POINTS)
def test_vanna_matches_fd_of_delta_in_vol(
    right: str, spot: float, vol: float, t: float, rate: float, carry: float
) -> None:
    h = 1e-5
    up = price_european(_spot_state(spot, vol + h, t, right, rate, carry)).delta
    down = price_european(_spot_state(spot, vol - h, t, right, rate, carry)).delta
    fd = (up - down) / (2.0 * h)
    assert price_european(_spot_state(spot, vol, t, right, rate, carry)).vanna == pytest.approx(
        fd, rel=1e-5, abs=1e-9
    )


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize(("spot", "vol", "t", "rate", "carry"), _SECOND_ORDER_POINTS)
def test_volga_matches_fd_of_vega_in_vol(
    right: str, spot: float, vol: float, t: float, rate: float, carry: float
) -> None:
    h = 1e-5
    up = price_european(_spot_state(spot, vol + h, t, right, rate, carry)).vega
    down = price_european(_spot_state(spot, vol - h, t, right, rate, carry)).vega
    fd = (up - down) / (2.0 * h)
    assert price_european(_spot_state(spot, vol, t, right, rate, carry)).volga == pytest.approx(
        fd, rel=1e-5, abs=1e-9
    )


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize(("spot", "vol", "t", "rate", "carry"), _SECOND_ORDER_POINTS)
def test_charm_matches_fd_of_delta_in_calendar_time(
    right: str, spot: float, vol: float, t: float, rate: float, carry: float
) -> None:
    h = 1e-5
    up = price_european(_spot_state(spot, vol, t + h, right, rate, carry)).delta
    down = price_european(_spot_state(spot, vol, t - h, right, rate, carry)).delta
    fd = -(up - down) / (2.0 * h)
    assert price_european(_spot_state(spot, vol, t, right, rate, carry)).charm == pytest.approx(
        fd, rel=1e-5, abs=1e-9
    )


def test_vanna_and_volga_are_call_put_identical_but_charm_is_not() -> None:
    call = price_european(ref_state("C"))
    put = price_european(ref_state("P"))
    assert call.vanna == pytest.approx(put.vanna, rel=1e-12)
    assert call.volga == pytest.approx(put.volga, rel=1e-12)
    assert call.charm != pytest.approx(put.charm, rel=1e-6)


@pytest.mark.parametrize("right", ["C", "P"])
def test_degenerate_state_has_zero_second_order_greeks(right: str) -> None:
    zero_vol = from_spot(
        spot=100.0, strike=95.0, maturity_years=0.5, volatility=0.0,
        discount_factor=0.99, option_right=right, carry=0.0,
    )
    g = price_european(zero_vol)
    assert (g.vanna, g.volga, g.charm) == (0.0, 0.0, 0.0)


_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _rt_vega_oracle(spot: float, vol: float, t: float, rate: float, carry: float) -> float:
    forward = spot * math.exp(carry * t)
    d1 = (math.log(forward / 100.0) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    return spot * _norm_pdf(d1) * math.exp((carry - rate) * t)


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize(("spot", "vol", "t", "rate", "carry"), _SECOND_ORDER_POINTS)
def test_rt_vega_equals_hand_built_running_time_core(
    right: str, spot: float, vol: float, t: float, rate: float, carry: float
) -> None:
    expected = _rt_vega_oracle(spot, vol, t, rate, carry)
    got = price_european(_spot_state(spot, vol, t, right, rate, carry)).rt_vega
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize(("spot", "vol", "t", "rate", "carry"), _SECOND_ORDER_POINTS)
def test_rt_vega_equals_vega_over_sqrt_t(
    right: str, spot: float, vol: float, t: float, rate: float, carry: float
) -> None:
    g = price_european(_spot_state(spot, vol, t, right, rate, carry))
    assert g.rt_vega == pytest.approx(g.vega / math.sqrt(t), rel=1e-12, abs=1e-12)


def test_rt_vega_is_maturity_comparable_where_raw_vega_is_not() -> None:
    short = price_european(_spot_state(100.0, 0.20, 0.25, "C", 0.0, 0.0))
    long = price_european(_spot_state(100.0, 0.20, 1.00, "C", 0.0, 0.0))
    assert long.vega / short.vega == pytest.approx(2.0, rel=0.05)
    assert long.rt_vega / short.rt_vega == pytest.approx(1.0, abs=0.05)


@pytest.mark.parametrize("right", ["C", "P"])
def test_rt_vega_is_zero_in_the_degenerate_t_to_zero_regime(right: str) -> None:
    zero_t = from_spot(
        spot=100.0, strike=95.0, maturity_years=0.0, volatility=0.20,
        discount_factor=1.0, option_right=right, carry=0.0,
    )
    zero_vol = from_spot(
        spot=100.0, strike=95.0, maturity_years=0.5, volatility=0.0,
        discount_factor=0.99, option_right=right, carry=0.0,
    )
    assert price_european(zero_t).rt_vega == 0.0
    assert price_european(zero_vol).rt_vega == 0.0


def test_american_call_no_dividend_equals_european() -> None:
    spot, strike, rate, vol, mat = 100.0, 95.0, 0.05, 0.25, 0.5
    df = math.exp(-rate * mat)
    euro = price_european(
        from_spot(spot=spot, strike=strike, maturity_years=mat, volatility=vol,
                  discount_factor=df, option_right="C", carry=rate)
    ).price
    amer = price_american(
        from_spot(spot=spot, strike=strike, maturity_years=mat, volatility=vol,
                  discount_factor=df, option_right="C", carry=rate, exercise_style="american")
    ).price
    assert amer == pytest.approx(euro, abs=0.02)


def test_american_put_carries_early_exercise_premium() -> None:
    spot, strike, rate, vol, mat = 80.0, 100.0, 0.08, 0.25, 1.0
    df = math.exp(-rate * mat)
    euro = price_european(
        from_spot(spot=spot, strike=strike, maturity_years=mat, volatility=vol,
                  discount_factor=df, option_right="P", carry=rate)
    ).price
    amer = price_american(
        from_spot(spot=spot, strike=strike, maturity_years=mat, volatility=vol,
                  discount_factor=df, option_right="P", carry=rate, exercise_style="american")
    ).price
    assert amer >= euro - 1e-6
    assert amer > euro


def test_price_dispatches_on_exercise_style() -> None:
    assert price(ref_state("C")).price == pytest.approx(price_european(ref_state("C")).price)
    amer_state = ref_state("C", exercise_style="american")
    assert price(amer_state).price == pytest.approx(price_american(amer_state).price, abs=1e-9)


def test_price_passes_explicit_steps_through_to_the_lattice() -> None:
    amer_state = ref_state("C", exercise_style="american")
    assert price(amer_state, steps=99).price == pytest.approx(
        price_american(amer_state, steps=99).price, abs=1e-12
    )


@pytest.mark.parametrize(
    "spot, strike, rate, vol, mat, right",
    [
        (100.0, 110.0, 0.05, 0.30, 0.5, "P"),
        (90.0, 100.0, 0.06, 0.25, 1.0, "P"),
        (100.0, 95.0, 0.05, 0.20, 0.5, "C"),
    ],
)
def test_bjerksund_stensland_matches_the_lattice(
    spot: float, strike: float, rate: float, vol: float, mat: float, right: str
) -> None:
    df = math.exp(-rate * mat)
    state = from_spot(spot=spot, strike=strike, maturity_years=mat, volatility=vol,
                      discount_factor=df, option_right=right, carry=rate,
                      exercise_style="american")
    lattice = price_american(state).price
    assert bjerksund_stensland_price(state) == pytest.approx(lattice, rel=1.5e-2)


@pytest.mark.parametrize("right", ["C", "P"])
def test_degenerate_states_collapse_to_discounted_intrinsic(right: str) -> None:
    state = from_forward(forward=110.0, strike=100.0, maturity_years=REF_T, volatility=0.0,
                         discount_factor=REF_DF, option_right=right, exercise_style="american")
    expected = REF_DF * (max(110.0 - 100.0, 0.0) if right == "C" else max(100.0 - 110.0, 0.0))
    assert price_american(state).price == pytest.approx(expected, abs=1e-12)
    assert bjerksund_stensland_price(state) == pytest.approx(expected, abs=1e-12)


def _option_key() -> InstrumentKey:
    return InstrumentKey(
        underlying_symbol="AAPL", security_type="OPT", exchange="SMART", currency="USD",
        multiplier=100.0, broker_contract_id="o-AAPL-C-100",
        expiry=date(2026, 6, 19), strike=100.0, option_right="C",
    )


def test_pricing_result_is_a_valid_stamped_contract() -> None:
    state = ref_state("C")
    greeks = price_european(state)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    a_stamp = stamp(
        calc_ts=snap_ts,
        code_version=PRICER_VERSION,
        config_hashes={"cfg": "cfg-hash-0"},
        source_records=(source_ref("market_state_snapshots", snap_ts, _option_key().canonical()),),
        source_timestamps=(snap_ts,),
    )
    result = pricing_result(
        state, greeks,
        snapshot_ts=snap_ts,
        contract_key=_option_key().canonical(),
        source_snapshot_ts=snap_ts,
        provenance=a_stamp,
    )
    assert isinstance(result, PricingResult)
    validate(result)
    assert table_for_contract(PricingResult) == "pricing_results"
    spot = state.spot
    assert result.dollar_delta == pytest.approx(greeks.delta * spot)
    assert result.dollar_gamma == pytest.approx(greeks.gamma * spot * spot / 100.0)
    assert result.dollar_vega == pytest.approx(greeks.vega * 0.01)
    assert result.dollar_theta == pytest.approx(greeks.theta / 365.0)
    assert result.dollar_rho == pytest.approx(greeks.rho * 0.01)
    assert result.dollar_gamma == pytest.approx(greeks.gamma * spot * spot / 100.0)
    assert result.dollar_gamma != pytest.approx(greeks.gamma * spot * spot)
    assert result.price == pytest.approx(greeks.price)
    assert result.pricer_version == PRICER_VERSION


def test_pricing_result_dollar_greeks_agree_with_the_canonical_home() -> None:
    state = ref_state("C")
    greeks = price_european(state)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    a_stamp = stamp(
        calc_ts=snap_ts,
        code_version=PRICER_VERSION,
        config_hashes={"cfg": "cfg-hash-0"},
        source_records=(source_ref("market_state_snapshots", snap_ts, _option_key().canonical()),),
        source_timestamps=(snap_ts,),
    )
    result = pricing_result(
        state, greeks,
        snapshot_ts=snap_ts,
        contract_key=_option_key().canonical(),
        source_snapshot_ts=snap_ts,
        provenance=a_stamp,
    )
    canonical = dollar_greeks(
        delta=greeks.delta, gamma=greeks.gamma, vega=greeks.vega, theta=greeks.theta,
        rho=greeks.rho, spot=state.spot, multiplier=1.0, quantity=1.0,
        config=MonetizationConfig(version="monetization-default"),
    )
    assert result.dollar_delta == pytest.approx(canonical.dollar_delta, rel=1e-15)
    assert result.dollar_gamma == pytest.approx(canonical.dollar_gamma, rel=1e-15)
    assert result.dollar_vega == pytest.approx(canonical.dollar_vega, rel=1e-15)
    assert result.dollar_theta == pytest.approx(canonical.dollar_theta, rel=1e-15)
    assert result.dollar_rho == pytest.approx(canonical.dollar_rho, rel=1e-15)


def test_pricing_result_carries_second_order_greeks_raw_and_cash() -> None:
    state = ref_state("C")
    greeks = price_european(state)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    a_stamp = stamp(
        calc_ts=snap_ts,
        code_version=PRICER_VERSION,
        config_hashes={"cfg": "cfg-hash-0"},
        source_records=(source_ref("market_state_snapshots", snap_ts, _option_key().canonical()),),
        source_timestamps=(snap_ts,),
    )
    result = pricing_result(
        state, greeks,
        snapshot_ts=snap_ts,
        contract_key=_option_key().canonical(),
        source_snapshot_ts=snap_ts,
        provenance=a_stamp,
    )
    canonical = dollar_greeks(
        delta=greeks.delta, gamma=greeks.gamma, vega=greeks.vega, theta=greeks.theta,
        rho=greeks.rho, spot=state.spot, vanna=greeks.vanna, volga=greeks.volga,
        charm=greeks.charm, rt_vega=greeks.rt_vega, multiplier=1.0, quantity=1.0,
        config=MonetizationConfig(version="monetization-default"),
    )
    assert result.vanna == greeks.vanna
    assert result.volga == greeks.volga
    assert result.charm == greeks.charm
    assert result.dollar_vanna == pytest.approx(canonical.dollar_vanna, rel=1e-15)
    assert result.dollar_volga == pytest.approx(canonical.dollar_volga, rel=1e-15)
    assert result.dollar_charm == pytest.approx(canonical.dollar_charm, rel=1e-15)
    assert result.rt_vega == greeks.rt_vega
    assert result.dollar_rt_vega == pytest.approx(canonical.dollar_rt_vega, rel=1e-15)


def test_pricing_state_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PricingState))
    assert names == (
        "forward", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "exercise_style", "spot", "carry",
    )


def test_price_greeks_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PriceGreeks))
    assert names == (
        "price", "delta", "gamma", "vega", "theta", "rho",
        "vanna", "volga", "charm",
        "rt_vega",
    )


def test_public_surface_is_frozen() -> None:
    assert set(pricing.__all__) == {
        "EXERCISE_STYLES", "PRICER_VERSION", "PriceGreeks", "PricingError",
        "PricingState", "bjerksund_stensland_price", "from_forward", "from_spot",
        "price", "price_american", "price_european", "price_european_array",
        "pricing_result",
        "UNIT_STRINGS", "DollarGreeks", "dollar_greeks", "gamma_unit_string",
        "theta_unit_string",
        "charm_unit_string",
    }


def test_entrypoint_signatures_are_frozen() -> None:
    assert set(inspect.signature(price).parameters) == {"state", "steps"}
    assert set(inspect.signature(from_spot).parameters) == {
        "spot", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "carry", "exercise_style",
    }
    assert set(inspect.signature(from_forward).parameters) == {
        "forward", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "spot", "exercise_style",
    }
    assert set(inspect.signature(pricing_result).parameters) == {
        "state", "greeks", "snapshot_ts", "contract_key", "source_snapshot_ts",
        "provenance",
    }


@pytest.mark.parametrize(
    "kwargs, bad_field",
    [
        (dict(forward=-1.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="C", spot=-1.0, carry=0.0), "forward"),
        (dict(forward=100.0, strike=0.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="C", spot=100.0, carry=0.0), "strike"),
        (dict(forward=100.0, strike=100.0, maturity_years=-0.1, volatility=0.2,
              discount_factor=0.99, option_right="C", spot=100.0, carry=0.0), "maturity_years"),
        (dict(forward=100.0, strike=100.0, maturity_years=0.25, volatility=-0.1,
              discount_factor=0.99, option_right="C", spot=100.0, carry=0.0), "volatility"),
        (dict(forward=100.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=1.5, option_right="C", spot=100.0, carry=0.0), "discount_factor"),
        (dict(forward=100.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="X", spot=100.0, carry=0.0), "option_right"),
        (dict(forward=100.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="C", exercise_style="bermudan",
              spot=100.0, carry=0.0), "exercise_style"),
        (dict(forward=200.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="C", spot=100.0, carry=0.0), "forward"),
        (dict(forward=100.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="C", spot=100.0, carry=math.inf), "carry"),
    ],
)
def test_malformed_state_is_refused(kwargs: dict, bad_field: str) -> None:
    with pytest.raises(PricingError) as excinfo:
        PricingState(exercise_style=kwargs.pop("exercise_style", "european"), **kwargs)
    assert excinfo.value.field == bad_field


def test_from_forward_with_spot_derives_carry() -> None:
    state = from_forward(forward=105.0, strike=100.0, maturity_years=0.5, volatility=0.2,
                         discount_factor=0.98, option_right="C", spot=100.0)
    assert state.spot == 100.0
    assert state.carry == pytest.approx(math.log(105.0 / 100.0) / 0.5, rel=1e-12)


def test_from_forward_zero_maturity_takes_zero_carry() -> None:
    state = from_forward(forward=100.0, strike=100.0, maturity_years=0.0, volatility=0.2,
                         discount_factor=1.0, option_right="C", spot=100.0)
    assert state.carry == 0.0
