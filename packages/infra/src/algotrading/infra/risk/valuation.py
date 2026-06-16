from __future__ import annotations

import math
from dataclasses import dataclass

from algotrading.infra.pricing import PricingState, from_spot

CONFIDENCE_OK = "ok"
CONFIDENCE_LOW = "low"
CONFIDENCE_LABELS = (CONFIDENCE_OK, CONFIDENCE_LOW)


class ValuationError(Exception):

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"valuation input {field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class ContractValuationInput:

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
        return self.spot * math.exp(self.carry * self.maturity_years)

    @property
    def implied_rate(self) -> float:
        if self.maturity_years <= 0.0:
            return 0.0
        return -math.log(self.discount_factor) / self.maturity_years


def pricing_state_for(valuation: ContractValuationInput) -> PricingState:
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
