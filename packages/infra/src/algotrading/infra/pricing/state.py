from __future__ import annotations

import math
from dataclasses import dataclass

from algotrading.infra.contracts import OPTION_RIGHTS

EXERCISE_STYLES = ("european", "american")

_FORWARD_CONSISTENCY_RTOL = 1e-9
_FORWARD_CONSISTENCY_ATOL = 1e-9


class PricingError(Exception):

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"pricing input {field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class PricingState:

    forward: float
    strike: float
    maturity_years: float
    volatility: float
    discount_factor: float
    option_right: str
    exercise_style: str
    spot: float
    carry: float

    def __post_init__(self) -> None:
        for name in ("forward", "strike", "spot"):
            value = getattr(self, name)
            if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0.0):
                raise PricingError(name, value, "must be a finite number strictly greater than 0")
        for name in ("maturity_years", "volatility"):
            value = getattr(self, name)
            if not (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0.0):
                raise PricingError(
                    name, value, "must be a finite number greater than or equal to 0"
                )
        if not (math.isfinite(self.carry)):
            raise PricingError("carry", self.carry, "must be a finite number")
        if not (0.0 < self.discount_factor <= 1.0):
            raise PricingError(
                "discount_factor", self.discount_factor, "must lie in the interval (0, 1]"
            )
        if self.option_right not in OPTION_RIGHTS:
            raise PricingError(
                "option_right", self.option_right, f"must be one of {OPTION_RIGHTS}"
            )
        if self.exercise_style not in EXERCISE_STYLES:
            raise PricingError(
                "exercise_style", self.exercise_style, f"must be one of {EXERCISE_STYLES}"
            )
        implied_forward = self.spot * math.exp(self.carry * self.maturity_years)
        if not math.isclose(
            self.forward,
            implied_forward,
            rel_tol=_FORWARD_CONSISTENCY_RTOL,
            abs_tol=_FORWARD_CONSISTENCY_ATOL,
        ):
            raise PricingError(
                "forward",
                self.forward,
                f"must equal spot * exp(carry * maturity_years) = {implied_forward!r}",
            )

    @property
    def is_call(self) -> bool:
        return self.option_right == "C"


@dataclass(frozen=True, slots=True)
class PriceGreeks:

    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    vanna: float = 0.0
    volga: float = 0.0
    charm: float = 0.0
    rt_vega: float = 0.0


def from_spot(
    *,
    spot: float,
    strike: float,
    maturity_years: float,
    volatility: float,
    discount_factor: float,
    option_right: str,
    carry: float,
    exercise_style: str = "european",
) -> PricingState:
    forward = spot * math.exp(carry * maturity_years)
    return PricingState(
        forward=forward,
        strike=strike,
        maturity_years=maturity_years,
        volatility=volatility,
        discount_factor=discount_factor,
        option_right=option_right,
        exercise_style=exercise_style,
        spot=spot,
        carry=carry,
    )


def from_forward(
    *,
    forward: float,
    strike: float,
    maturity_years: float,
    volatility: float,
    discount_factor: float,
    option_right: str,
    spot: float | None = None,
    exercise_style: str = "european",
) -> PricingState:
    if spot is None:
        spot, carry = forward, 0.0
    elif maturity_years <= 0.0:
        carry = 0.0
    else:
        carry = math.log(forward / spot) / maturity_years
    return PricingState(
        forward=forward,
        strike=strike,
        maturity_years=maturity_years,
        volatility=volatility,
        discount_factor=discount_factor,
        option_right=option_right,
        exercise_style=exercise_style,
        spot=spot,
        carry=carry,
    )
