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
}


@dataclass(frozen=True, slots=True)
class DollarGreeks:
    """The five monetized Greeks, each beside the unit string it is quoted in."""

    dollar_delta: float
    dollar_gamma: float
    dollar_vega: float
    dollar_theta: float
    dollar_rho: float
    gamma_unit: str
    theta_unit: str


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


def gamma_unit_string(normalisation: str) -> str:
    """The unit string for Gamma\\$ under the chosen normalisation."""
    return UNIT_STRINGS[f"dollar_gamma_{normalisation}"]


def theta_unit_string(day_count: int) -> str:
    """The unit string for Theta\\$ under the chosen day-count."""
    return UNIT_STRINGS[f"dollar_theta_{day_count}"]


def dollar_greeks(
    *,
    delta: float,
    gamma: float,
    vega: float,
    theta: float,
    rho: float,
    spot: float,
    multiplier: float = 1.0,
    quantity: float = 1.0,
    config: MonetizationConfig,
) -> DollarGreeks:
    """Monetize one contract/position's raw Greeks under the configured conventions.

    The two convention forks come from ``config``: ``gamma_normalisation`` and
    ``theta_day_count``. Per-contract is ``quantity=1.0``; per-position passes the signed
    held quantity; a book is the additive sum of per-position numbers.
    """
    return DollarGreeks(
        dollar_delta=dollar_delta(delta, spot, multiplier, quantity),
        dollar_gamma=dollar_gamma(
            gamma, spot, multiplier, quantity, normalisation=config.gamma_normalisation
        ),
        dollar_vega=dollar_vega(vega, multiplier, quantity),
        dollar_theta=dollar_theta(theta, multiplier, quantity, day_count=config.theta_day_count),
        dollar_rho=dollar_rho(rho, multiplier, quantity),
        gamma_unit=gamma_unit_string(config.gamma_normalisation),
        theta_unit=theta_unit_string(config.theta_day_count),
    )
