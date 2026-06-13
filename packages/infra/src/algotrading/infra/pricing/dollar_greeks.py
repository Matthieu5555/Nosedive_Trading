"""The canonical $-Greek conventions (P0.2 / OQ-1, ADR 0036).

Raw per-unit Greeks are the source of truth; the dollar layer is a *derived* view, each
number quoted in an explicit unit. This module is the single home of those five
conversions and of the two genuine convention forks, driven by
:class:`~algotrading.core.config.MonetizationConfig`:

* **Delta\\$** ``= delta * S * mult`` — per \\$1 of underlying.
* **Gamma\\$** ``= gamma * S**2 / 100`` (``gamma_normalisation="one_pct"``, the default,
  per **1% move**) or ``gamma * S**2`` (``"one_dollar"``, per \\$1 move).
* **Vega\\$** ``= vega * 0.01 * mult`` — per **1 vol point** (0.01).
* **Theta\\$** ``= theta * mult / day_count`` — per **calendar day** with ``day_count=365``
  (the default), or per trading day with ``day_count=252``.
* **Rho\\$** ``= rho * 0.01 * mult`` — per **1% rate**.

The second-order set (TARGET §7.2) is monetized in the *same* "raw Greek times one
standard shock times multiplier" style, each in an explicit unit:

* **Vanna\\$** ``= vanna * S * 0.01 * mult`` — the change in **Delta\\$ per 1 vol point**
  (equivalently the change in Vega\\$ per a $1-of-underlying move): ``d(delta*S)/dsigma``
  for a 0.01 vol step.
* **Volga\\$** ``= volga * 0.01**2 * mult`` — the change in **Vega\\$ per 1 vol point**:
  ``d(vega*0.01)/dsigma`` for a 0.01 vol step.
* **Charm\\$** ``= charm * S * mult / day_count`` — the change in **Delta\\$ per day**
  (``ddelta/dt`` monetized like delta and put on theta's calendar/trading day-count fork).

Per-contract numbers (``mult``) scale to per-position by ``* quantity``, and per-position
numbers are additive across a book — the Phase-2 basket builder sums positions without
reworking this contract. Each value carries a matching unit string (:data:`UNIT_STRINGS`)
when it crosses the BFF boundary, so the front never receives a bare float.

The function is a pure conversion: it reads no wall clock and consumes the flags from the
passed config, so two runs with the same inputs and the same ``MonetizationConfig`` produce
identical dollar numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from algotrading.core.config import MonetizationConfig

# The unit string each dollar number carries to the front (the BFF metric contract).
UNIT_STRINGS: dict[str, str] = {
    "dollar_delta": "$ per $1 of underlying",
    "dollar_gamma_one_pct": "$ per 1% move",
    "dollar_gamma_one_dollar": "$ per $1 move",
    "dollar_vega": "$ per 1 vol point",
    "dollar_theta_365": "$ per calendar day",
    "dollar_theta_252": "$ per trading day",
    "dollar_rho": "$ per 1% rate",
    # Second-order set (TARGET §7.2). Vanna/Volga carry no convention fork (one unit
    # each); Charm rides the same calendar/trading day-count fork as Theta.
    "dollar_vanna": "$ delta per 1 vol point",
    "dollar_volga": "$ vega per 1 vol point",
    "dollar_charm_365": "$ delta per calendar day",
    "dollar_charm_252": "$ delta per trading day",
}


@dataclass(frozen=True, slots=True)
class DollarGreeks:
    """The monetized Greeks, each beside the unit string of its forked convention.

    The five first-order numbers plus the three second-order ones (vanna/volga/charm,
    TARGET §7.2). Only the *forked* units are carried as fields (``gamma_unit``,
    ``theta_unit``, ``charm_unit`` — the conventions that a config flag can flip); the
    unforked ones (delta/vega/rho, vanna/volga) are fixed and looked up in
    :data:`UNIT_STRINGS`, so this object never carries a unit a caller could not derive.
    """

    dollar_delta: float
    dollar_gamma: float
    dollar_vega: float
    dollar_theta: float
    dollar_rho: float
    dollar_vanna: float
    dollar_volga: float
    dollar_charm: float
    gamma_unit: str
    theta_unit: str
    charm_unit: str


def dollar_delta(
    delta: float, spot: float, multiplier: float = 1.0, quantity: float = 1.0
) -> float:
    """Delta\\$ = Δ·S·mult·qty — per \\$1 of underlying."""
    return delta * spot * multiplier * quantity


def dollar_gamma(
    gamma: float,
    spot: float,
    multiplier: float = 1.0,
    quantity: float = 1.0,
    *,
    normalisation: str = "one_pct",
) -> float:
    """Gamma\\$ — per 1% move (``one_pct``: Γ·S²/100) or per \\$1 move (``one_dollar``: Γ·S²)."""
    base = gamma * spot * spot * multiplier * quantity
    return base / 100.0 if normalisation == "one_pct" else base


def dollar_vega(vega: float, multiplier: float = 1.0, quantity: float = 1.0) -> float:
    """Vega\\$ = vega·0.01·mult·qty — per 1 vol point (0.01)."""
    return vega * 0.01 * multiplier * quantity


def dollar_theta(
    theta: float, multiplier: float = 1.0, quantity: float = 1.0, *, day_count: int = 365
) -> float:
    """Theta\\$ = theta·mult·qty / day_count — per calendar (365) or trading (252) day."""
    return theta * multiplier * quantity / day_count


def dollar_rho(rho: float, multiplier: float = 1.0, quantity: float = 1.0) -> float:
    """Rho\\$ = rho·0.01·mult·qty — per 1% rate."""
    return rho * 0.01 * multiplier * quantity


def dollar_vanna(
    vanna: float, spot: float, multiplier: float = 1.0, quantity: float = 1.0
) -> float:
    """Vanna\\$ = vanna·S·0.01·mult·qty — change in Delta\\$ per 1 vol point."""
    return vanna * spot * 0.01 * multiplier * quantity


def dollar_volga(volga: float, multiplier: float = 1.0, quantity: float = 1.0) -> float:
    """Volga\\$ = volga·0.01²·mult·qty — change in Vega\\$ per 1 vol point."""
    return volga * 0.01 * 0.01 * multiplier * quantity


def dollar_charm(
    charm: float,
    spot: float,
    multiplier: float = 1.0,
    quantity: float = 1.0,
    *,
    day_count: int = 365,
) -> float:
    """Charm\\$ = charm·S·mult·qty / day_count — change in Delta\\$ per calendar/trading day."""
    return charm * spot * multiplier * quantity / day_count


def gamma_unit_string(normalisation: str) -> str:
    """The unit string for Gamma\\$ under the chosen normalisation."""
    return UNIT_STRINGS[f"dollar_gamma_{normalisation}"]


def theta_unit_string(day_count: int) -> str:
    """The unit string for Theta\\$ under the chosen day-count."""
    return UNIT_STRINGS[f"dollar_theta_{day_count}"]


def charm_unit_string(day_count: int) -> str:
    """The unit string for Charm\\$ under the chosen day-count (the theta fork)."""
    return UNIT_STRINGS[f"dollar_charm_{day_count}"]


def dollar_greeks(
    *,
    delta: float,
    gamma: float,
    vega: float,
    theta: float,
    rho: float,
    spot: float,
    vanna: float = 0.0,
    volga: float = 0.0,
    charm: float = 0.0,
    multiplier: float = 1.0,
    quantity: float = 1.0,
    config: MonetizationConfig,
) -> DollarGreeks:
    """Monetize one contract/position's raw Greeks under the configured conventions.

    The two convention forks come from ``config``: ``gamma_normalisation`` and
    ``theta_day_count`` (Charm\\$ rides the latter, since charm is a per-time Greek like
    theta). Per-contract is ``quantity=1.0``; per-position passes the signed held
    quantity; a book is the additive sum of per-position numbers. ``vanna``/``volga``/
    ``charm`` default to ``0.0`` so a first-order-only caller is unchanged; the pricing
    emission path passes the analytic second-order values (TARGET §7.2).
    """
    return DollarGreeks(
        dollar_delta=dollar_delta(delta, spot, multiplier, quantity),
        dollar_gamma=dollar_gamma(
            gamma, spot, multiplier, quantity, normalisation=config.gamma_normalisation
        ),
        dollar_vega=dollar_vega(vega, multiplier, quantity),
        dollar_theta=dollar_theta(theta, multiplier, quantity, day_count=config.theta_day_count),
        dollar_rho=dollar_rho(rho, multiplier, quantity),
        dollar_vanna=dollar_vanna(vanna, spot, multiplier, quantity),
        dollar_volga=dollar_volga(volga, multiplier, quantity),
        dollar_charm=dollar_charm(
            charm, spot, multiplier, quantity, day_count=config.theta_day_count
        ),
        gamma_unit=gamma_unit_string(config.gamma_normalisation),
        theta_unit=theta_unit_string(config.theta_day_count),
        charm_unit=charm_unit_string(config.theta_day_count),
    )
