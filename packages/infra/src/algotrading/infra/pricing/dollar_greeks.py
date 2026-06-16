from __future__ import annotations

from dataclasses import dataclass

from algotrading.core.config import MonetizationConfig

UNIT_STRINGS: dict[str, str] = {
    "dollar_delta": "$ per $1 of underlying",
    "dollar_gamma_one_pct": "$ per 1% move",
    "dollar_gamma_one_dollar": "$ per $1 move",
    "dollar_vega": "$ per 1 vol point",
    "dollar_rt_vega": "$ per 1 vol point",
    "dollar_theta_365": "$ per calendar day",
    "dollar_theta_252": "$ per trading day",
    "dollar_rho": "$ per 1% rate",
    "dollar_vanna": "$ delta per 1 vol point",
    "dollar_volga": "$ vega per 1 vol point",
    "dollar_charm_365": "$ delta per calendar day",
    "dollar_charm_252": "$ delta per trading day",
}


@dataclass(frozen=True, slots=True)
class DollarGreeks:

    dollar_delta: float
    dollar_gamma: float
    dollar_vega: float
    dollar_theta: float
    dollar_rho: float
    dollar_vanna: float
    dollar_volga: float
    dollar_charm: float
    dollar_rt_vega: float
    gamma_unit: str
    theta_unit: str
    charm_unit: str


def dollar_delta(
    delta: float, spot: float, multiplier: float = 1.0, quantity: float = 1.0
) -> float:
    return delta * spot * multiplier * quantity


def dollar_gamma(
    gamma: float,
    spot: float,
    multiplier: float = 1.0,
    quantity: float = 1.0,
    *,
    normalisation: str = "one_pct",
) -> float:
    base = gamma * spot * spot * multiplier * quantity
    return base / 100.0 if normalisation == "one_pct" else base


def dollar_vega(vega: float, multiplier: float = 1.0, quantity: float = 1.0) -> float:
    return vega * 0.01 * multiplier * quantity


def dollar_rt_vega(rt_vega: float, multiplier: float = 1.0, quantity: float = 1.0) -> float:
    return rt_vega * 0.01 * multiplier * quantity


def dollar_theta(
    theta: float, multiplier: float = 1.0, quantity: float = 1.0, *, day_count: int = 365
) -> float:
    return theta * multiplier * quantity / day_count


def dollar_rho(rho: float, multiplier: float = 1.0, quantity: float = 1.0) -> float:
    return rho * 0.01 * multiplier * quantity


def dollar_vanna(
    vanna: float, spot: float, multiplier: float = 1.0, quantity: float = 1.0
) -> float:
    return vanna * spot * 0.01 * multiplier * quantity


def dollar_volga(volga: float, multiplier: float = 1.0, quantity: float = 1.0) -> float:
    return volga * 0.01 * 0.01 * multiplier * quantity


def dollar_charm(
    charm: float,
    spot: float,
    multiplier: float = 1.0,
    quantity: float = 1.0,
    *,
    day_count: int = 365,
) -> float:
    return charm * spot * multiplier * quantity / day_count


def gamma_unit_string(normalisation: str) -> str:
    return UNIT_STRINGS[f"dollar_gamma_{normalisation}"]


def theta_unit_string(day_count: int) -> str:
    return UNIT_STRINGS[f"dollar_theta_{day_count}"]


def charm_unit_string(day_count: int) -> str:
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
    rt_vega: float = 0.0,
    multiplier: float = 1.0,
    quantity: float = 1.0,
    config: MonetizationConfig,
) -> DollarGreeks:
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
        dollar_rt_vega=dollar_rt_vega(rt_vega, multiplier, quantity),
        gamma_unit=gamma_unit_string(config.gamma_normalisation),
        theta_unit=theta_unit_string(config.theta_day_count),
        charm_unit=charm_unit_string(config.theta_day_count),
    )
