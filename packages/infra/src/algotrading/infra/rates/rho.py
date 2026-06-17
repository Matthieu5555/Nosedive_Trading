"""External-curve Rho — the sensitivity to the ingested risk-free curve, bumped per currency.

ADR 0054, ruling 5: Rho becomes the sensitivity to the **external** curve `r(T)`, not to the
per-expiry parity-implied rate. The parity-implied rate stays the pricing-consistency rate and is
never displaced; the external curve is the **risk** rate, and a book-level "rates +50bp" bumps
*this* curve per currency.

`external_curve_rho` evaluates `r(T)` on the external curve at the option's maturity, then takes a
symmetric finite-difference of the Black-76 price under a `±bump` shift of that external rate. The
forward is **held fixed** under the bump (the carry absorbs the shift, so the forward the chain
reconstructed stays put); only the discounting `exp(-r·T)` moves. This keeps the two rates separate
by construction: the curve rate drives this risk number, the parity-implied rate drives pricing.
"""

from __future__ import annotations

import math

from algotrading.infra.pricing.black76 import price_european
from algotrading.infra.pricing.state import PricingState

from .curve import RateCurve

# Default bump for the symmetric finite difference (1bp), in absolute rate. Per-unit-rate Rho is the
# derivative ∂Price/∂r; multiply by 0.01 downstream for the per-1%-rate convention (ADR 0036).
DEFAULT_RATE_BUMP = 1e-4


class ExternalRhoError(ValueError):
    """The external-curve Rho cannot be computed."""


def _reprice_at_external_rate(state: PricingState, external_rate: float) -> float:
    """Reprice holding the forward fixed; the discount factor follows the external rate."""
    maturity = state.maturity_years
    discount_factor = math.exp(-external_rate * maturity)
    # Hold the forward fixed: forward = spot * exp(carry * T) must still hold, so the carry is
    # re-derived from the (unchanged) forward and spot. Only the discounting moved.
    carry = math.log(state.forward / state.spot) / maturity if maturity > 0.0 else 0.0
    bumped = PricingState(
        forward=state.forward,
        strike=state.strike,
        maturity_years=maturity,
        volatility=state.volatility,
        discount_factor=discount_factor,
        option_right=state.option_right,
        exercise_style=state.exercise_style,
        spot=state.spot,
        carry=carry,
    )
    return price_european(bumped).price


def external_curve_rho(
    state: PricingState,
    curve: RateCurve,
    *,
    bump: float = DEFAULT_RATE_BUMP,
) -> float:
    """Per-unit-rate Rho against the external curve: `∂Price/∂r` by symmetric finite difference.

    Evaluates the curve at `state.maturity_years` to get the base external rate, then reprices at
    `r ± bump` and returns the centred difference. Units are price per unit (1.0) of rate; the
    per-1%-rate dollar convention multiplies by 0.01 downstream (ADR 0036).
    """
    if not (math.isfinite(bump) and bump > 0.0):
        raise ExternalRhoError(f"bump must be finite positive, got {bump!r}")
    if state.maturity_years <= 0.0:
        return 0.0
    base_rate = curve.rate_at(state.maturity_years)
    up = _reprice_at_external_rate(state, base_rate + bump)
    down = _reprice_at_external_rate(state, base_rate - bump)
    return (up - down) / (2.0 * bump)
