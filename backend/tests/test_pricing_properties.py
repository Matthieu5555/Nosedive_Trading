"""Property-based tests for the pricing invariants (Workstream C owns these).

These assert relations that must hold across a range of inputs, not at three
hand-picked points: put-call parity, Greek signs and bounds, monotonicity of price
in volatility, and American >= European. The expected relation is the oracle; no
value is copied from the engine.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pricing import price_american, price_european
from pricing.state import from_forward, from_spot

# Shared strategies for well-conditioned, in-domain option inputs.
forwards = st.floats(min_value=10.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
strikes = st.floats(min_value=10.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
maturities = st.floats(min_value=0.01, max_value=5.0)
vols = st.floats(min_value=0.01, max_value=2.0)
rates = st.floats(min_value=0.0, max_value=0.15)
rights = st.sampled_from(["C", "P"])


@given(f=forwards, k=strikes, t=maturities, vol=vols, rate=rates)
@settings(max_examples=200)
def test_put_call_parity_holds(f: float, k: float, t: float, vol: float, rate: float) -> None:
    # C - P == DF * (F - K), exactly, for any (F, K, T, sigma) in Black-76.
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
    # With carry == rate (non-dividend, b == r), spot call delta == N(d1) in [0, 1].
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
    # Parametrize the strike by standardized moneyness z = ln(K/F) / (sigma*sqrt(T)),
    # bounded to +/-2, so every option has real optionality and vega is solidly
    # positive. Deep in/out of the money the price underflows and two values tie in
    # float, which is a resolution limit, not a monotonicity failure. Strict
    # monotonicity over the well-conditioned region is what makes IV invertible.
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
    moneyness=st.floats(min_value=1.0, max_value=1.4),  # strike / spot: ATM to 40% ITM
    t=st.floats(min_value=0.3, max_value=2.0),
    vol=st.floats(min_value=0.1, max_value=0.5),
    rate=st.floats(min_value=0.05, max_value=0.15),
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_american_put_premium_is_positive(
    spot: float, moneyness: float, t: float, vol: float, rate: float
) -> None:
    # American >= European, tested where the early-exercise premium is STRUCTURAL,
    # not numerical noise: an at- or in-the-money American put under a positive rate
    # is genuinely worth exercising early (you collect the strike's interest sooner).
    #
    # Why not compare a non-dividend call (premium == 0) or compare on a shared
    # lattice? QuantLib injects the American exercise's stopping times into the time
    # grid, so the American and European trees are *different* discretizations, each
    # carrying ~2e-2 of error; in the zero-premium regime that noise swamps the
    # relation (and a same-lattice comparison is not slack-free -- it violates by
    # ~1.4e-2). Here the structural premium is >= ~0.07 across the sampled region
    # (stress-checked offline), an order of magnitude above the lattice error, so the
    # inequality holds with no arbitrary slack and the test still has teeth: it
    # asserts the premium is strictly positive, which a broken early-exercise path
    # (one that just returned the European value) would fail. The premium -> 0
    # no-early-exercise limit is pinned separately by
    # test_american_call_no_dividend_equals_european.
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
    assert amer > euro  # a real, positive early-exercise premium
