"""The valuation input risk prices a position against, and the adapter to the pricer.

The risk core does not reach into the six analytics contracts directly; it takes one
typed, resolved market state per contract — :class:`ContractValuationInput` — and turns
it into the pricer's :class:`algotrading.infra.pricing.PricingState`. Keeping the risk
input a single owned dataclass (rather than a tuple of snapshot + forward + surface
objects) is what keeps the risk functions pure and testable from fixtures, and confines
the join against the analytics contracts to one thin assembly step.

The state is built through :func:`algotrading.infra.pricing.from_spot`, so the forward
is *derived* from spot and carry and the pricer's ``forward == spot * exp(carry * T)``
invariant holds by construction — risk can never hand the pricer an internally
inconsistent state. Spot and carry are therefore the stored anchors; the forward is a
derived view (:attr:`ContractValuationInput.forward`).

This module is the *only* place in ``risk`` that binds the pricing seam. If M2 freezes a
pricing interface whose names differ, this adapter is the single point to reconcile —
the rest of the package depends on :class:`ContractValuationInput`, not on the pricer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from algotrading.infra.pricing import PricingState, from_spot

# Confidence labels carried from quote QC: a position on a contract QC flagged
# low-confidence is still priced, but the label rides through to the line so it can
# be surfaced, never silently dropped.
CONFIDENCE_OK = "ok"
CONFIDENCE_LOW = "low"
CONFIDENCE_LABELS = (CONFIDENCE_OK, CONFIDENCE_LOW)


class ValuationError(Exception):
    """A valuation input was malformed. Carries the field, value, and reason."""

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"valuation input {field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class ContractValuationInput:
    """The resolved market state needed to price one contract, plus its identity.

    ``spot`` and ``carry`` are the anchors; ``forward`` is derived from them (see
    the module docstring). ``multiplier`` and ``currency`` come from the
    instrument and drive monetization; ``confidence`` carries the quote-QC verdict
    so a low-confidence line is labeled rather than dropped.
    """

    contract_key: str
    underlying: str
    option_right: str
    exercise_style: str
    strike: float
    maturity_years: float
    spot: float
    carry: float
    volatility: float
    discount_factor: float
    multiplier: float
    currency: str
    confidence: str = CONFIDENCE_OK

    def __post_init__(self) -> None:
        if self.multiplier <= 0.0 or not math.isfinite(self.multiplier):
            raise ValuationError(
                "multiplier", self.multiplier, "must be a finite number greater than 0"
            )
        if not self.currency:
            raise ValuationError("currency", self.currency, "must be a non-empty currency code")
        if self.confidence not in CONFIDENCE_LABELS:
            raise ValuationError(
                "confidence", self.confidence, f"must be one of {CONFIDENCE_LABELS}"
            )

    @property
    def forward(self) -> float:
        """The forward to expiry, derived from spot and carry (the pricer's anchor)."""
        return self.spot * math.exp(self.carry * self.maturity_years)

    @property
    def implied_rate(self) -> float:
        """Continuously compounded rate implied by the discount factor (0 at T == 0)."""
        if self.maturity_years <= 0.0:
            return 0.0
        return -math.log(self.discount_factor) / self.maturity_years


def pricing_state_for(valuation: ContractValuationInput) -> PricingState:
    """Build the pricer's state vector from a valuation input.

    Uses :func:`algotrading.infra.pricing.from_spot` so the forward is derived
    consistently and the pricer's forward-consistency invariant cannot be violated.
    """
    return from_spot(
        spot=valuation.spot,
        strike=valuation.strike,
        maturity_years=valuation.maturity_years,
        volatility=valuation.volatility,
        discount_factor=valuation.discount_factor,
        option_right=valuation.option_right,
        carry=valuation.carry,
        exercise_style=valuation.exercise_style,
    )
