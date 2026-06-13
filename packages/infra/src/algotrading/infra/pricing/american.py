"""American option pricing via a QuantLib binomial lattice (Leisen-Reimer).

QuantLib does the genuine heavy lifting here — the lattice and the early-exercise
logic — and this module is the typed glue that maps :class:`pricing.state.PricingState`
onto it and back, holding the same conventions as the European engine. The
Leisen-Reimer tree (defined for an odd step count) converges far faster and more
smoothly than Cox-Ross-Rubinstein for vanilla options, so the no-early-exercise
limit recovers the European price tightly. Price, delta, gamma, and theta come
straight from the tree; vega and rho are central finite differences (the binomial
engine does not expose them). The Bjerksund-Stensland closed-form approximation is
offered as an optional fast price path (:func:`bjerksund_stensland_price`),
cross-checked against the lattice in tests.

The valuation date is a fixed constant, never the wall clock: the price depends
only on the year fraction to expiry, which is reconstructed from
``maturity_years`` under Actual/365, so two runs of the same state are identical.
"""

from __future__ import annotations

import math

import QuantLib as ql

from .black76 import _discounted_intrinsic
from .state import PriceGreeks, PricingState

# A fixed anchor date. Only the span to expiry matters; the absolute date does not.
_VALUATION_DATE = ql.Date(15, 1, 2025)
_DAY_COUNT = ql.Actual365Fixed()
_CALENDAR = ql.NullCalendar()

# Odd, as the Leisen-Reimer tree requires. 513 clears a node-placement resonance
# seen near the strike at a few hundred steps (a ~1-cent error at 257) and prices
# vanilla American options to ~1e-6 of the European twin in the no-exercise limit.
_DEFAULT_STEPS = 513
_VEGA_BUMP = 1e-3  # in vol units; vega is reported per 1.00 of vol
_RHO_BUMP = 1e-4   # in rate units; rho holds the dividend yield fixed


def _expiry_for(maturity_years: float) -> ql.Date:
    """The expiry date whose Actual/365 fraction reproduces ``maturity_years``."""
    return _VALUATION_DATE + max(1, int(round(maturity_years * 365.0)))


def _build(
    state: PricingState, rate: float, dividend_yield: float, *, fast: bool, steps: int
) -> tuple[ql.VanillaOption, ql.SimpleQuote, ql.SimpleQuote, ql.SimpleQuote]:
    """Assemble the QuantLib option and its live spot/vol/rate quotes."""
    ql.Settings.instance().evaluationDate = _VALUATION_DATE
    spot_quote = ql.SimpleQuote(state.spot)
    vol_quote = ql.SimpleQuote(state.volatility)
    rate_quote = ql.SimpleQuote(rate)
    dividend_quote = ql.SimpleQuote(dividend_yield)
    risk_free = ql.YieldTermStructureHandle(
        ql.FlatForward(_VALUATION_DATE, ql.QuoteHandle(rate_quote), _DAY_COUNT)
    )
    dividend = ql.YieldTermStructureHandle(
        ql.FlatForward(_VALUATION_DATE, ql.QuoteHandle(dividend_quote), _DAY_COUNT)
    )
    vol = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(_VALUATION_DATE, _CALENDAR, ql.QuoteHandle(vol_quote), _DAY_COUNT)
    )
    process = ql.BlackScholesMertonProcess(ql.QuoteHandle(spot_quote), dividend, risk_free, vol)
    option_type = ql.Option.Call if state.is_call else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(option_type, state.strike)
    exercise = ql.AmericanExercise(_VALUATION_DATE, _expiry_for(state.maturity_years))
    option = ql.VanillaOption(payoff, exercise)
    engine = (
        ql.BjerksundStenslandApproximationEngine(process)
        if fast
        else ql.BinomialVanillaEngine(process, "lr", steps if steps % 2 == 1 else steps + 1)
    )
    option.setPricingEngine(engine)
    return option, spot_quote, vol_quote, rate_quote


def _central_difference(option: ql.VanillaOption, quote: ql.SimpleQuote, bump: float) -> float:
    """Central difference of the option NPV in one quote, restoring the base value."""
    base = quote.value()
    quote.setValue(base + bump)
    up = option.NPV()
    quote.setValue(base - bump)
    down = option.NPV()
    quote.setValue(base)
    return (up - down) / (2.0 * bump)


def price_american(state: PricingState, *, steps: int = _DEFAULT_STEPS) -> PriceGreeks:
    """Price one American option and its Greeks on a Leisen-Reimer lattice.

    ``steps`` is the number of binomial time steps (forced odd, as Leisen-Reimer
    requires); more steps trade run time for a finer early-exercise boundary.
    Degenerate states (no time or no vol) collapse to the discounted intrinsic, the
    same total-function behavior as the European engine, since there is no
    early-exercise value without time or vol.
    """
    if state.maturity_years <= 0.0 or state.volatility <= 0.0:
        return _discounted_intrinsic(state)

    rate = -math.log(state.discount_factor) / state.maturity_years
    dividend_yield = rate - state.carry  # b == r - q  =>  q == r - b
    option, _spot_quote, vol_quote, rate_quote = _build(
        state, rate, dividend_yield, fast=False, steps=steps
    )
    price = option.NPV()
    vega = _central_difference(option, vol_quote, _VEGA_BUMP)
    rho = _central_difference(option, rate_quote, _RHO_BUMP)
    return PriceGreeks(
        price=price,
        delta=option.delta(),
        gamma=option.gamma(),
        vega=vega,
        # QuantLib theta is dV/dt per year (time decay), matching our convention.
        theta=option.theta(),
        rho=rho,
        # Second-order cross/convexity Greeks (vanna/volga/charm) are a closed-form
        # Black-76 European feature (TARGET §7.2); the lattice does not expose them and
        # this lane does not finite-difference them off the tree. Left explicitly 0.0 —
        # a documented gap, not a silent zero — so an American line never reports a
        # spurious vanna. (Carry to the American path is out of this lane's scope.)
        vanna=0.0,
        volga=0.0,
        charm=0.0,
    )


def bjerksund_stensland_price(state: PricingState) -> float:
    """Fast Bjerksund-Stensland closed-form approximation of the American price.

    The optional fast path: an analytic approximation that avoids building a
    lattice. Returns the price only; callers wanting Greeks use the lattice engine.
    Cross-checked against the lattice in the test suite.
    """
    if state.maturity_years <= 0.0 or state.volatility <= 0.0:
        return _discounted_intrinsic(state).price
    rate = -math.log(state.discount_factor) / state.maturity_years
    dividend_yield = rate - state.carry
    option, _spot, _vol, _rate = _build(state, rate, dividend_yield, fast=True, steps=0)
    return float(option.NPV())
