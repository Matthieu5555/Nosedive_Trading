"""The frozen pricing interface: a typed state vector in, price + Greeks out.

This is the keystone the rest of Workstream C and all of Workstream D build
against, so its shape is deliberately pinned (see ``tests/test_pricing.py`` for the
shape pin-test). Nothing else in the platform is allowed to turn a state vector
into a price.

Unit conventions, stated once and asserted by the convention tests:

* ``volatility`` is an annualized decimal, ``0.20`` meaning 20% — never a percent.
  A value of ``20.0`` is a 2000%-vol input, not 20%, and prices accordingly.
* ``maturity_years`` is a year fraction, ``0.25`` meaning three months — never a
  day count. ``maturity_years = 30`` is thirty years, not thirty days.
* ``discount_factor`` is ``exp(-r * maturity_years)`` for the continuously
  compounded rate ``r``; it lives in ``(0, 1]``. The engine discounts with this
  directly, so the pricer never needs a rate except to derive ``r`` for the
  American lattice and rho.
* ``carry`` is the cost of carry ``b`` (Haug's generalized Black-Scholes-Merton):
  ``b = r`` for a non-dividend equity, ``b = 0`` for a future (Black-76),
  ``b = r - q`` for a continuous dividend yield ``q``.
* ``forward`` is the forward to expiry and is the authoritative anchor for the
  European price. It must be consistent with spot and carry: ``forward ==
  spot * exp(carry * maturity_years)``. The constructor enforces this so the
  forward-form price and the spot-form Greeks can never silently disagree.

Greek conventions (see :class:`PriceGreeks`): ``delta`` is spot delta
(``dPrice/dspot``), ``gamma`` is ``d2Price/dspot2``, ``vega`` is per 1.00 of vol
(divide by 100 for a one-vol-point move), ``theta`` is per year of calendar time
(``dPrice/dt``, i.e. time decay, so it is negative for most long options; divide
by 365 for a one-day figure), and ``rho`` is per 1.00 of the rate, holding the
forward fixed. The second-order set (``vanna``, ``volga``, ``charm``) extends the
same units — vanna/volga per 1.00 of vol (the vega clock), charm per year (the
theta clock); see :class:`PriceGreeks` for the full definitions. ``rt_vega`` is the
running-time (annualised) vega ``vega / sqrt(T)`` (ADR 0050), in the same per-1.00-of-vol
unit as ``vega`` — vega with the ``sqrt(T)`` factor stripped, so it is comparable across
maturities; ``0.0`` in the degenerate (``T -> 0``) regime.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from algotrading.infra.contracts import OPTION_RIGHTS

# The two exercise styles the engine knows. A state with any other style is a bug,
# not a thing to price, so it is refused at construction.
EXERCISE_STYLES = ("european", "american")

# Relative tolerance for the forward == spot * exp(carry * T) consistency check.
# Loose enough to absorb float round-trips through ln/exp, tight enough that a real
# mismatch (a forward that does not match the supplied spot and carry) is caught.
_FORWARD_CONSISTENCY_RTOL = 1e-9
_FORWARD_CONSISTENCY_ATOL = 1e-9


class PricingError(Exception):
    """A pricing input was malformed (bad right, style, or out-of-domain number).

    Carries the offending field, its value, and a plain-language reason, mirroring
    the contract-layer errors so a rejection says exactly what was wrong.
    """

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"pricing input {field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class PricingState:
    """One option's full state vector: everything needed to price it, once.

    Frozen and validated at construction, so an instance is always a well-formed,
    internally consistent thing to price. Build one with :func:`from_forward` when
    you think in forward space (the IV solver, the forward engine) or with
    :func:`from_spot` when you have a spot and a carry.
    """

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
        """True for a call, False for a put."""
        return self.option_right == "C"


@dataclass(frozen=True, slots=True)
class PriceGreeks:
    """Model price and the first- and second-order Greeks for one option.

    Conventions are documented on :mod:`pricing.state`. In short: spot delta,
    spot gamma, vega per 1.00 vol, theta per year (time decay), rho per 1.00 rate.

    The three second-order cross/convexity Greeks (TARGET §7.2) extend the same
    unit system (``documentation/blueprint/02-math-framework.md``: "All first-order
    and second-order sensitivities should be computed in a unified unit system"):

    * ``vanna`` = ``d2Price/dspot dsigma`` = ``ddelta/dsigma`` — per 1.00 of vol, the
      same vol unit as ``vega`` (the cross-sensitivity of delta to a vol move).
    * ``volga`` (vomma) = ``d2Price/dsigma2`` = ``dvega/dsigma`` — per 1.00 of vol
      squared (the convexity of vega in vol).
    * ``charm`` = ``ddelta/dt`` (delta decay) — per **year** of calendar time, the
      same per-year clock as ``theta`` (``ddelta/dt = -ddelta/dT``), so a roll-down
      bleeds delta the way ``theta`` bleeds value; divide by 365 for a one-day figure.

    ``rt_vega`` (running-time / annualised vega, ADR 0050) = ``vega / sqrt(T)`` =
    ``S · N'(d1) · e^{(b-r)T}`` — vega with the ``sqrt(T)`` maturity factor stripped, in
    the *same* per-1.00-of-vol unit as ``vega``. Raw vega scales with ``sqrt(T)`` and so is
    not comparable across tenors; RT-Vega removes that mechanical time-growth, so a vega
    figure can be read straight across the maturity grid. At ``T -> 0`` it is defined to be
    ``0.0`` (a guard, not a ``0/0``): the degenerate regime has ``vega == 0``, no vol
    sensitivity at all, so its time-normalised value is zero too.

    They default to ``0.0`` so the legacy ``PriceGreeks(price, delta, gamma, vega,
    theta, rho)`` construction still type-checks; the closed-form Black-76 engine
    fills them with the analytic values, while engines that do not yet expose them
    (the American lattice, the finite-difference cross-check) leave them ``0.0``
    *explicitly* rather than silently — a documented gap, not a hidden zero.
    """

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
    """Build a state from a spot and a carry; the forward is derived consistently."""
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
    """Build a state from a forward; carry is derived from the forward and spot.

    With no ``spot`` given, spot defaults to the forward and carry to zero — the
    pure forward (Black-76 / futures) view, which is all the European price and the
    IV solver need. Pass a real ``spot`` to get a meaningful carry and spot-space
    Greeks.
    """
    if spot is None:
        spot, carry = forward, 0.0
    elif maturity_years <= 0.0:
        # With no time, forward and spot must coincide; carry is undefined, take 0.
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
