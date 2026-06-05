"""Forward-consistent Black-76 European price and Greeks, in closed form.

The price is the forward form ``DF * (F*N(d1) - K*N(d2))`` (roadmap Eqs 8-11),
which is exact in the forward and discount factor. The Greeks are the generalized
Black-Scholes-Merton (Haug) partials with cost of carry ``b``; they are consistent
with the forward-form price because :class:`pricing.state.PricingState` pins
``forward == spot * exp(carry * maturity_years)``. Conventions (spot delta, spot
gamma, vega per 1.00 vol, per-year theta, forward-fixed rho) are documented on
:mod:`pricing.state` and asserted by the convention and finite-difference tests.

Degenerate inputs are handled explicitly rather than left to divide-by-zero: with
``sigma == 0`` or ``maturity_years == 0`` the option is worth its discounted
intrinsic with no convexity, so the engine stays total over its whole domain.
"""

from __future__ import annotations

import math

from .state import PriceGreeks, PricingState

_INV_SQRT_TWO_PI = 1.0 / math.sqrt(2.0 * math.pi)


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function (matches the fixture oracle)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normal_pdf(x: float) -> float:
    """Standard normal PDF."""
    return _INV_SQRT_TWO_PI * math.exp(-0.5 * x * x)


def _implied_rate(discount_factor: float, maturity_years: float) -> float:
    """Continuously compounded rate implied by the discount factor (0 at T == 0)."""
    if maturity_years <= 0.0:
        return 0.0
    return -math.log(discount_factor) / maturity_years


def _discounted_intrinsic(state: PricingState) -> PriceGreeks:
    """Price and Greeks in the degenerate (zero vol or zero maturity) regime.

    The value is the discounted intrinsic; there is no gamma or vega. Delta is the
    discounted-intrinsic spot sensitivity (the carry-adjusted in-the-money
    indicator), theta is the discount unwinding, and rho stays the forward-fixed
    ``-T * price``.
    """
    forward, strike, df = state.forward, state.strike, state.discount_factor
    maturity = state.maturity_years
    rate = _implied_rate(df, maturity)
    if state.is_call:
        intrinsic = max(forward - strike, 0.0)
        in_the_money = forward > strike
        sign = 1.0
    else:
        intrinsic = max(strike - forward, 0.0)
        in_the_money = forward < strike
        sign = -1.0
    price = df * intrinsic
    delta = sign * math.exp((state.carry - rate) * maturity) if in_the_money else 0.0
    return PriceGreeks(
        price=price, delta=delta, gamma=0.0, vega=0.0, theta=rate * price, rho=-maturity * price
    )


def price_european(state: PricingState) -> PriceGreeks:
    """Price one European option and its Greeks via the forward-consistent Black-76."""
    if state.maturity_years <= 0.0 or state.volatility <= 0.0:
        return _discounted_intrinsic(state)

    forward, strike, df = state.forward, state.strike, state.discount_factor
    maturity, sigma, spot = state.maturity_years, state.volatility, state.spot
    rate = _implied_rate(df, maturity)
    sqrt_t = math.sqrt(maturity)
    vol_sqrt_t = sigma * sqrt_t
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * maturity) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    cdf_d1, cdf_d2 = _normal_cdf(d1), _normal_cdf(d2)
    pdf_d1 = _normal_pdf(d1)
    # e^{(b-r)T} == (forward / spot) * df; the carry-and-discount factor on spot Greeks.
    carry_discount = math.exp((state.carry - rate) * maturity)
    decay = -spot * carry_discount * pdf_d1 * sigma / (2.0 * sqrt_t)  # shared theta term

    if state.is_call:
        price = df * (forward * cdf_d1 - strike * cdf_d2)
        delta = carry_discount * cdf_d1
        theta = (
            decay
            - (state.carry - rate) * spot * carry_discount * cdf_d1
            - rate * strike * df * cdf_d2
        )
    else:
        price = df * (strike * (1.0 - cdf_d2) - forward * (1.0 - cdf_d1))
        delta = -carry_discount * (1.0 - cdf_d1)
        theta = (
            decay
            + (state.carry - rate) * spot * carry_discount * (1.0 - cdf_d1)
            + rate * strike * df * (1.0 - cdf_d2)
        )

    gamma = carry_discount * pdf_d1 / (spot * vol_sqrt_t)
    vega = spot * carry_discount * pdf_d1 * sqrt_t
    rho = -maturity * price
    return PriceGreeks(price=price, delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)
