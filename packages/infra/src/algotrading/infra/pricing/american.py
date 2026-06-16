from __future__ import annotations

import math

import QuantLib as ql

from .black76 import _discounted_intrinsic
from .state import PriceGreeks, PricingState

_VALUATION_DATE = ql.Date(15, 1, 2025)
_DAY_COUNT = ql.Actual365Fixed()
_CALENDAR = ql.NullCalendar()

_DEFAULT_STEPS = 513
_VEGA_BUMP = 1e-3
_RHO_BUMP = 1e-4


def _expiry_for(maturity_years: float) -> ql.Date:
    return _VALUATION_DATE + max(1, int(round(maturity_years * 365.0)))


def _build(
    state: PricingState, rate: float, dividend_yield: float, *, fast: bool, steps: int
) -> tuple[ql.VanillaOption, ql.SimpleQuote, ql.SimpleQuote, ql.SimpleQuote]:
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
    base = quote.value()
    quote.setValue(base + bump)
    up = option.NPV()
    quote.setValue(base - bump)
    down = option.NPV()
    quote.setValue(base)
    return (up - down) / (2.0 * bump)


def price_american(state: PricingState, *, steps: int = _DEFAULT_STEPS) -> PriceGreeks:
    if state.maturity_years <= 0.0 or state.volatility <= 0.0:
        return _discounted_intrinsic(state)

    rate = -math.log(state.discount_factor) / state.maturity_years
    dividend_yield = rate - state.carry
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
        theta=option.theta(),
        rho=rho,
        vanna=0.0,
        volga=0.0,
        charm=0.0,
        rt_vega=0.0,
    )


def bjerksund_stensland_price(state: PricingState) -> float:
    if state.maturity_years <= 0.0 or state.volatility <= 0.0:
        return _discounted_intrinsic(state).price
    rate = -math.log(state.discount_factor) / state.maturity_years
    dividend_yield = rate - state.carry
    option, _spot, _vol, _rate = _build(state, rate, dividend_yield, fast=True, steps=0)
    return float(option.NPV())
