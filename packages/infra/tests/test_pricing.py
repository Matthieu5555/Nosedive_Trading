"""Reference, limiting-case, convention, and adapter tests for the pricing engine.

Independent oracles (never the code under test):

* ``fixtures.synthetic.black_call``/``black_put`` — a second, closed-form Black-76
  implementation (it uses ``math.erf`` and lives in the fixture library, not in
  ``pricing``). Used directly for the price and, via central finite difference, for
  the Greeks.
* Hull, *Options, Futures, and Other Derivatives*, example 15.6 — a textbook
  reference value for a European call/put.
* The closed-form European engine vs the QuantLib American lattice — two different
  code paths that must agree in the no-early-exercise limit (a non-dividend
  American call equals its European twin).

Float comparisons use explicit tolerances sized to each oracle's precision.
"""

from __future__ import annotations

import dataclasses
import inspect
import math
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra import pricing
from algotrading.infra.contracts import InstrumentKey, PricingResult, table_for_contract, validate
from algotrading.infra.pricing import (
    PRICER_VERSION,
    PriceGreeks,
    PricingError,
    PricingState,
    bjerksund_stensland_price,
    price,
    price_american,
    price_european,
    pricing_result,
)
from algotrading.infra.pricing.state import from_forward, from_spot
from fixtures.synthetic import black_call, black_put

# The canonical reference point, cross-checked across three Black-76 engines in
# onboarding (fixture == vollib == ql.blackFormula == 3.947884 to 6 dp).
REF_F, REF_K, REF_T, REF_VOL, REF_DF = 100.0, 100.0, 0.25, 0.20, 0.99
REF_RATE = -math.log(REF_DF) / REF_T  # continuously compounded r implied by DF


def ref_state(option_right: str, exercise_style: str = "european") -> PricingState:
    """The reference state for a call or put (spot == forward, carry == 0)."""
    return from_forward(
        forward=REF_F,
        strike=REF_K,
        maturity_years=REF_T,
        volatility=REF_VOL,
        discount_factor=REF_DF,
        option_right=option_right,
        exercise_style=exercise_style,
    )


# --------------------------------------------------------------------------- #
# Reference values                                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("right", ["C", "P"])
def test_european_price_matches_independent_black76(right: str) -> None:
    # Oracle: the fixture's own Black-76 closed form (different code).
    oracle = (black_call if right == "C" else black_put)(REF_F, REF_K, REF_T, REF_VOL, REF_DF)
    got = price_european(ref_state(right)).price
    assert got == pytest.approx(oracle, rel=1e-10)
    # At the money with these inputs the call and put are equal (F == K), ~3.9479.
    assert got == pytest.approx(3.947884, abs=1e-5)


def test_european_matches_hull_textbook_example() -> None:
    # Hull example 15.6: S=42, K=40, r=0.10, sigma=0.20, T=0.5 (non-dividend, b=r).
    # Published values: call = 4.76, put = 0.81.
    spot, strike, rate, vol, mat = 42.0, 40.0, 0.10, 0.20, 0.5
    df = math.exp(-rate * mat)
    call = price_european(
        from_spot(
            spot=spot, strike=strike, maturity_years=mat, volatility=vol,
            discount_factor=df, option_right="C", carry=rate,  # b = r, no dividend
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
    # Put-call parity as a second, internal cross-check: C - P == DF*(F - K).
    forward = spot * math.exp(rate * mat)
    assert call - put == pytest.approx(df * (forward - strike), rel=1e-10)


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize(
    ("rate", "div_yield"),
    [(0.05, 0.0), (0.05, 0.03)],  # b == r (no dividend) and b == r - q (dividend yield)
)
def test_black76_and_black_scholes_agree_under_the_documented_carry(
    right: str, rate: float, div_yield: float
) -> None:
    # The documented carry convention (pricing.state): b == r for a non-dividend
    # equity, b == 0 for a future (Black-76), b == r - q under a continuous dividend
    # yield q. A European price is a function of (forward, strike, T, sigma, DF) alone,
    # so pricing the SAME option two ways must agree: Black-Scholes from a spot with
    # carry b, and Black-76 from the forward F = spot * exp(b * T) with carry 0. Oracle:
    # the fixture's closed-form Black-76 on that forward (different code). Discounting
    # is always at r, never the carry.
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
            discount_factor=df, option_right=right,  # spot -> forward, carry -> 0
        )
    ).price
    oracle = (black_call if right == "C" else black_put)(forward, strike, mat, vol, df)
    assert black_scholes == pytest.approx(oracle, rel=1e-10)
    assert black76 == pytest.approx(oracle, rel=1e-10)
    assert black_scholes == pytest.approx(black76, rel=1e-12)


# --------------------------------------------------------------------------- #
# Limiting cases                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("right", ["C", "P"])
def test_zero_vol_gives_discounted_intrinsic(right: str) -> None:
    # sigma -> 0: the option is worth its discounted intrinsic, with no convexity.
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
    # T -> 0 (with DF == 1, as economics require): price collapses to intrinsic.
    state = from_forward(
        forward=107.0, strike=100.0, maturity_years=0.0, volatility=REF_VOL,
        discount_factor=1.0, option_right=right,
    )
    expected = max(107.0 - 100.0, 0.0) if right == "C" else max(100.0 - 107.0, 0.0)
    assert price_european(state).price == pytest.approx(expected, abs=1e-12)


def test_deep_itm_call_approaches_discounted_forward_minus_strike() -> None:
    # K -> 0: a call is almost the discounted forward; the put is almost worthless.
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
    # sigma -> infinity: N(d1) -> 1, N(d2) -> 0, so a call -> DF * F. At sigma=20
    # the residual is ~3e-5 (well inside 1e-3) yet still strictly below the asymptote.
    call = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=REF_T,
                     volatility=20.0, discount_factor=REF_DF, option_right="C")
    ).price
    assert call == pytest.approx(REF_DF * REF_F, rel=1e-3)
    assert call < REF_DF * REF_F  # strictly below the asymptote


# --------------------------------------------------------------------------- #
# Convention guards: the bugs these would catch are explicit                  #
# --------------------------------------------------------------------------- #
def test_volatility_is_decimal_not_percent() -> None:
    # 0.20 means 20%. Code that treated the input as a percent (dividing by 100,
    # pricing sigma=0.002) would return ~0 for this ATM option; code that scaled up
    # (sigma=20.0) would saturate near DF*F. Pinning the exact value catches both.
    assert price_european(ref_state("C")).price == pytest.approx(3.947884, abs=1e-5)
    saturated = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=REF_T,
                     volatility=20.0, discount_factor=REF_DF, option_right="C")
    ).price
    near_intrinsic = price_european(
        from_forward(forward=REF_F, strike=REF_K, maturity_years=REF_T,
                     volatility=0.002, discount_factor=REF_DF, option_right="C")
    ).price
    assert near_intrinsic < 0.1 < 3.947884 < saturated  # the three regimes are distinct


def test_maturity_is_years_not_days() -> None:
    # 0.25 means a quarter-year. Code that read it as days (0.25/365 years) would
    # return near-intrinsic (~0 ATM); the true value is ~3.95 and rises with T.
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


# --------------------------------------------------------------------------- #
# Greeks vs finite difference of the independent (fixture) price               #
# --------------------------------------------------------------------------- #
def _fd_forward_first(fn: Callable[[float], float], x: float, h: float) -> float:
    return (fn(x + h) - fn(x - h)) / (2.0 * h)


def _fixture_price(
    right: str, *, f: float, t: float, vol: float, df: float, k: float = REF_K
) -> float:
    return (black_call if right == "C" else black_put)(f, k, t, vol, df)


@pytest.mark.parametrize("right", ["C", "P"])
def test_delta_matches_fd_of_independent_price(right: str) -> None:
    # At carry == 0 (spot == forward) spot delta equals forward delta = d(price)/dF.
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
    # vega is per 1.00 of vol; the fixture FD is in the same units.
    assert price_european(ref_state(right)).vega == pytest.approx(fd, rel=1e-4)


@pytest.mark.parametrize("right", ["C", "P"])
def test_theta_matches_spot_fixed_fd(right: str) -> None:
    # Spot-fixed theta at carry == 0: spot (== forward) is held fixed as T varies,
    # while the discount factor tracks T at the implied rate. theta = -d(price)/dT.
    def px(t: float) -> float:
        return _fixture_price(right, f=REF_F, t=t, vol=REF_VOL, df=math.exp(-REF_RATE * t))

    fd = -_fd_forward_first(px, REF_T, 1e-4)
    assert price_european(ref_state(right)).theta == pytest.approx(fd, abs=1e-3)
    assert price_european(ref_state(right)).theta < 0.0  # long option decays


@pytest.mark.parametrize("right", ["C", "P"])
def test_rho_matches_fd_in_rate_forward_fixed(right: str) -> None:
    # Forward-fixed rho: only the discount factor responds to r, so rho = -T*price.
    def px(rate: float) -> float:
        return _fixture_price(right, f=REF_F, t=REF_T, vol=REF_VOL, df=math.exp(-rate * REF_T))

    fd = _fd_forward_first(px, REF_RATE, 1e-6)
    assert price_european(ref_state(right)).rho == pytest.approx(fd, abs=1e-4)


# --------------------------------------------------------------------------- #
# American engine                                                             #
# --------------------------------------------------------------------------- #
def test_american_call_no_dividend_equals_european() -> None:
    # A non-dividend American call (carry == rate, so q == 0) is never exercised
    # early, so it must equal its European twin. European is the closed-form engine,
    # American is the QuantLib lattice — two different code paths agreeing.
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
    assert amer == pytest.approx(euro, abs=0.02)  # lattice discretization tolerance


def test_american_put_carries_early_exercise_premium() -> None:
    # A deep-in-the-money American put on a non-dividend underlying CAN be worth
    # exercising early, so it is worth at least its European twin (premium >= 0).
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
    assert amer > euro  # there is a real early-exercise premium for this fixture


def test_price_dispatches_on_exercise_style() -> None:
    assert price(ref_state("C")).price == pytest.approx(price_european(ref_state("C")).price)
    amer_state = ref_state("C", exercise_style="american")
    assert price(amer_state).price == pytest.approx(price_american(amer_state).price, abs=1e-9)


def test_price_passes_explicit_steps_through_to_the_lattice() -> None:
    # When steps is given, dispatch must use exactly that count (not the default).
    amer_state = ref_state("C", exercise_style="american")
    assert price(amer_state, steps=99).price == pytest.approx(
        price_american(amer_state, steps=99).price, abs=1e-12
    )


@pytest.mark.parametrize(
    "spot, strike, rate, vol, mat, right",
    [
        (100.0, 110.0, 0.05, 0.30, 0.5, "P"),  # ITM American put with a real premium
        (90.0, 100.0, 0.06, 0.25, 1.0, "P"),
        (100.0, 95.0, 0.05, 0.20, 0.5, "C"),   # non-dividend call: no early exercise
    ],
)
def test_bjerksund_stensland_matches_the_lattice(
    spot: float, strike: float, rate: float, vol: float, mat: float, right: str
) -> None:
    # The closed-form approximation is cross-checked against the QuantLib lattice
    # (a different engine — the independent oracle). They agree to ~1% for American
    # options; the no-early-exercise call agrees far more tightly.
    df = math.exp(-rate * mat)
    state = from_spot(spot=spot, strike=strike, maturity_years=mat, volatility=vol,
                      discount_factor=df, option_right=right, carry=rate,
                      exercise_style="american")
    lattice = price_american(state).price
    assert bjerksund_stensland_price(state) == pytest.approx(lattice, rel=1.5e-2)


@pytest.mark.parametrize("right", ["C", "P"])
def test_degenerate_states_collapse_to_discounted_intrinsic(right: str) -> None:
    # Zero vol on the lattice and the fast path both give the discounted intrinsic,
    # the same total-function behavior as the European engine.
    state = from_forward(forward=110.0, strike=100.0, maturity_years=REF_T, volatility=0.0,
                         discount_factor=REF_DF, option_right=right, exercise_style="american")
    expected = REF_DF * (max(110.0 - 100.0, 0.0) if right == "C" else max(100.0 - 110.0, 0.0))
    assert price_american(state).price == pytest.approx(expected, abs=1e-12)
    assert bjerksund_stensland_price(state) == pytest.approx(expected, abs=1e-12)


# --------------------------------------------------------------------------- #
# Contract adapter                                                            #
# --------------------------------------------------------------------------- #
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
        config_hash="cfg-hash-0",
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
    validate(result)  # raises if any field rule is violated
    assert table_for_contract(PricingResult) == "pricing_results"
    # Cash Greeks use the documented per-unit-of-underlying conventions.
    assert result.cash_delta == pytest.approx(greeks.delta * state.spot)
    assert result.cash_gamma == pytest.approx(greeks.gamma * state.spot * state.spot)
    assert result.cash_vega == pytest.approx(greeks.vega * 0.01)
    assert result.price == pytest.approx(greeks.price)
    assert result.pricer_version == PRICER_VERSION


# --------------------------------------------------------------------------- #
# Frozen-interface pin: a change here breaks D's suite loudly (by design)      #
# --------------------------------------------------------------------------- #
def test_pricing_state_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PricingState))
    assert names == (
        "forward", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "exercise_style", "spot", "carry",
    )


def test_price_greeks_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PriceGreeks))
    assert names == ("price", "delta", "gamma", "vega", "theta", "rho")


def test_public_surface_is_frozen() -> None:
    # The exact set of names D and the IV solver import from ``pricing``. Adding or
    # dropping a public symbol is a deliberate interface change; a break here is the
    # early warning that D's own pin (on D's branch) would otherwise be first to hit.
    assert set(pricing.__all__) == {
        "EXERCISE_STYLES", "PRICER_VERSION", "PriceGreeks", "PricingError",
        "PricingState", "bjerksund_stensland_price", "from_forward", "from_spot",
        "price", "price_american", "price_european", "pricing_result",
    }


def test_entrypoint_signatures_are_frozen() -> None:
    # Callers pass these by keyword, so a renamed or removed parameter is a silent
    # break; pin the parameter *names* (order among keyword-only args is irrelevant).
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


# --------------------------------------------------------------------------- #
# Negative paths: malformed inputs are refused, with a labeled reason          #
# --------------------------------------------------------------------------- #
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
        # Inconsistent forward: spot*exp(carry*T) != forward.
        (dict(forward=200.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="C", spot=100.0, carry=0.0), "forward"),
        # A non-finite carry is refused before the forward-consistency check.
        (dict(forward=100.0, strike=100.0, maturity_years=0.25, volatility=0.2,
              discount_factor=0.99, option_right="C", spot=100.0, carry=math.inf), "carry"),
    ],
)
def test_malformed_state_is_refused(kwargs: dict, bad_field: str) -> None:
    with pytest.raises(PricingError) as excinfo:
        PricingState(exercise_style=kwargs.pop("exercise_style", "european"), **kwargs)
    assert excinfo.value.field == bad_field


def test_from_forward_with_spot_derives_carry() -> None:
    # With a real spot and positive maturity, carry is ln(F/spot)/T and spot is kept.
    state = from_forward(forward=105.0, strike=100.0, maturity_years=0.5, volatility=0.2,
                         discount_factor=0.98, option_right="C", spot=100.0)
    assert state.spot == 100.0
    assert state.carry == pytest.approx(math.log(105.0 / 100.0) / 0.5, rel=1e-12)


def test_from_forward_zero_maturity_takes_zero_carry() -> None:
    # At zero maturity forward and spot coincide and carry is undefined -> taken as 0.
    state = from_forward(forward=100.0, strike=100.0, maturity_years=0.0, volatility=0.2,
                         discount_factor=1.0, option_right="C", spot=100.0)
    assert state.carry == 0.0
