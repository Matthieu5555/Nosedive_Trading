from __future__ import annotations

import math

import pytest
from algotrading.infra.pricing import price_american, price_european
from algotrading.infra.pricing.state import from_forward, from_spot
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

forwards = st.floats(min_value=10.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
strikes = st.floats(min_value=10.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
maturities = st.floats(min_value=0.01, max_value=5.0)
vols = st.floats(min_value=0.01, max_value=2.0)
rates = st.floats(min_value=0.0, max_value=0.15)
rights = st.sampled_from(["C", "P"])


@given(f=forwards, k=strikes, t=maturities, vol=vols, rate=rates)
@settings(max_examples=200)
def test_put_call_parity_holds(f: float, k: float, t: float, vol: float, rate: float) -> None:
    df = math.exp(-rate * t)
    call = price_european(
        from_forward(forward=f, strike=k, maturity_years=t, volatility=vol,
                     discount_factor=df, option_right="C")
    ).price
    put = price_european(
        from_forward(forward=f, strike=k, maturity_years=t, volatility=vol,
                     discount_factor=df, option_right="P")
    ).price
    assert call - put == pytest.approx(df * (f - k), rel=1e-9, abs=1e-9)


@given(f=forwards, k=strikes, t=maturities, vol=vols, rate=rates, right=rights)
@settings(max_examples=200)
def test_gamma_and_vega_are_non_negative(
    f: float, k: float, t: float, vol: float, rate: float, right: str
) -> None:
    greeks = price_european(
        from_forward(forward=f, strike=k, maturity_years=t, volatility=vol,
                     discount_factor=math.exp(-rate * t), option_right=right)
    )
    assert greeks.gamma >= -1e-12
    assert greeks.vega >= -1e-12


@given(spot=forwards, k=strikes, t=maturities, vol=vols, rate=rates)
@settings(max_examples=200)
def test_call_delta_in_unit_interval(
    spot: float, k: float, t: float, vol: float, rate: float
) -> None:
    delta = price_european(
        from_spot(spot=spot, strike=k, maturity_years=t, volatility=vol,
                  discount_factor=math.exp(-rate * t), option_right="C", carry=rate)
    ).delta
    assert -1e-12 <= delta <= 1.0 + 1e-12


@given(spot=forwards, k=strikes, t=maturities, vol=vols, rate=rates)
@settings(max_examples=200)
def test_put_delta_in_negative_unit_interval(
    spot: float, k: float, t: float, vol: float, rate: float
) -> None:
    delta = price_european(
        from_spot(spot=spot, strike=k, maturity_years=t, volatility=vol,
                  discount_factor=math.exp(-rate * t), option_right="P", carry=rate)
    ).delta
    assert -1.0 - 1e-12 <= delta <= 1e-12


@given(
    f=st.floats(min_value=50.0, max_value=200.0),
    std_moneyness=st.floats(min_value=-2.0, max_value=2.0),
    t=maturities,
    rate=rates,
    right=rights,
    vol=st.floats(min_value=0.1, max_value=1.0),
    bump=st.floats(min_value=1e-3, max_value=0.2),
)
@settings(max_examples=200)
def test_price_strictly_increases_in_volatility(
    f: float, std_moneyness: float, t: float, rate: float, right: str, vol: float, bump: float
) -> None:
    strike = f * math.exp(std_moneyness * vol * math.sqrt(t))
    df = math.exp(-rate * t)
    low = price_european(
        from_forward(forward=f, strike=strike, maturity_years=t, volatility=vol,
                     discount_factor=df, option_right=right)
    ).price
    high = price_european(
        from_forward(forward=f, strike=strike, maturity_years=t, volatility=vol + bump,
                     discount_factor=df, option_right=right)
    ).price
    assert high > low


@given(
    spot=st.floats(min_value=50.0, max_value=150.0),
    moneyness=st.floats(min_value=1.0, max_value=1.4),
    t=st.floats(min_value=0.3, max_value=2.0),
    vol=st.floats(min_value=0.1, max_value=0.5),
    rate=st.floats(min_value=0.05, max_value=0.15),
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_american_put_premium_is_positive(
    spot: float, moneyness: float, t: float, vol: float, rate: float
) -> None:
    strike = spot * moneyness
    df = math.exp(-rate * t)
    euro = price_european(
        from_spot(spot=spot, strike=strike, maturity_years=t, volatility=vol,
                  discount_factor=df, option_right="P", carry=rate)
    ).price
    amer = price_american(
        from_spot(spot=spot, strike=strike, maturity_years=t, volatility=vol,
                  discount_factor=df, option_right="P", carry=rate,
                  exercise_style="american")
    ).price
    assert amer > euro
