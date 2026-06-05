from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import erf, exp, log, pi, sqrt
from statistics import mean


ONE_YEAR_DAYS = 365.0


@dataclass(frozen=True)
class UnderlyingQuote:
    symbol: str
    name: str
    asset_class: str
    bid: float
    ask: float
    last: float
    volume: int
    timestamp: str


@dataclass(frozen=True)
class OptionQuote:
    underlying: str
    expiry: str
    strike: float
    right: str
    bid: float
    ask: float
    last: float
    implied_vol: float
    open_interest: int
    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass(frozen=True)
class SurfacePoint:
    underlying: str
    expiry: str
    strike: float
    log_moneyness: float
    implied_vol: float
    total_variance: float


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    average_cost: float
    market_price: float
    asset_class: str


@dataclass(frozen=True)
class OrderTicket:
    symbol: str
    action: str
    quantity: int
    order_type: str
    limit_price: float | None
    transmit: bool


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def option_mid(option: OptionQuote) -> float:
    if option.bid > 0 and option.ask > 0 and option.ask >= option.bid:
        return (option.bid + option.ask) / 2.0
    return option.last


def quote_mid(quote: UnderlyingQuote) -> float:
    if quote.bid > 0 and quote.ask > 0 and quote.ask >= quote.bid:
        return (quote.bid + quote.ask) / 2.0
    return quote.last


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def normal_pdf(value: float) -> float:
    return exp(-0.5 * value * value) / sqrt(2.0 * pi)


def years_to_expiry(expiry: str, now: datetime | None = None) -> float:
    current = now or datetime.now(UTC)
    expiry_dt = datetime.fromisoformat(expiry).replace(tzinfo=UTC)
    days = max((expiry_dt - current).total_seconds() / 86_400.0, 1.0)
    return days / ONE_YEAR_DAYS


def black_scholes_price(
    spot: float,
    strike: float,
    maturity_years: float,
    volatility: float,
    right: str,
    rate: float = 0.0,
) -> float:
    sigma = max(volatility, 0.0001)
    tau = max(maturity_years, 1.0 / ONE_YEAR_DAYS)
    d1 = (log(spot / strike) + (rate + 0.5 * sigma * sigma) * tau) / (sigma * sqrt(tau))
    d2 = d1 - sigma * sqrt(tau)
    if right.upper() == "C":
        return spot * normal_cdf(d1) - strike * exp(-rate * tau) * normal_cdf(d2)
    return strike * exp(-rate * tau) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def greeks(
    spot: float,
    strike: float,
    maturity_years: float,
    volatility: float,
    right: str,
    rate: float = 0.0,
) -> dict[str, float]:
    sigma = max(volatility, 0.0001)
    tau = max(maturity_years, 1.0 / ONE_YEAR_DAYS)
    d1 = (log(spot / strike) + (rate + 0.5 * sigma * sigma) * tau) / (sigma * sqrt(tau))
    d2 = d1 - sigma * sqrt(tau)
    pdf = normal_pdf(d1)
    delta = normal_cdf(d1) if right.upper() == "C" else normal_cdf(d1) - 1.0
    gamma = pdf / (spot * sigma * sqrt(tau))
    vega = spot * pdf * sqrt(tau) / 100.0
    if right.upper() == "C":
        theta = (
            -(spot * pdf * sigma) / (2.0 * sqrt(tau))
            - rate * strike * exp(-rate * tau) * normal_cdf(d2)
        ) / 365.0
    else:
        theta = (
            -(spot * pdf * sigma) / (2.0 * sqrt(tau))
            + rate * strike * exp(-rate * tau) * normal_cdf(-d2)
        ) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def build_surface_points(
    underlyings: list[UnderlyingQuote],
    options: list[OptionQuote],
    now: datetime | None = None,
) -> list[SurfacePoint]:
    spot_by_symbol = {quote.symbol: quote_mid(quote) for quote in underlyings}
    points: list[SurfacePoint] = []
    for option in options:
        spot = spot_by_symbol.get(option.underlying)
        if spot is None or spot <= 0:
            continue
        maturity = years_to_expiry(option.expiry, now)
        points.append(
            SurfacePoint(
                underlying=option.underlying,
                expiry=option.expiry,
                strike=option.strike,
                log_moneyness=log(option.strike / spot),
                implied_vol=option.implied_vol,
                total_variance=option.implied_vol * option.implied_vol * maturity,
            )
        )
    return points


def risk_summary(positions: list[Position], options: list[OptionQuote]) -> dict[str, object]:
    option_by_symbol = {
        f"{option.underlying}-{option.expiry}-{option.strike:.0f}-{option.right}": option
        for option in options
    }
    lines: list[dict[str, object]] = []
    totals = {"market_value": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    for position in positions:
        option = option_by_symbol.get(position.symbol)
        multiplier = 100.0 if option else 1.0
        market_value = position.quantity * position.market_price * multiplier
        delta = position.quantity * (option.delta if option else 1.0) * multiplier
        gamma = position.quantity * (option.gamma if option else 0.0) * multiplier
        vega = position.quantity * (option.vega if option else 0.0) * multiplier
        theta = position.quantity * (option.theta if option else 0.0) * multiplier
        totals["market_value"] += market_value
        totals["delta"] += delta
        totals["gamma"] += gamma
        totals["vega"] += vega
        totals["theta"] += theta
        lines.append(
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "marketPrice": round(position.market_price, 4),
                "marketValue": round(market_value, 2),
                "delta": round(delta, 2),
                "gamma": round(gamma, 4),
                "vega": round(vega, 2),
                "theta": round(theta, 2),
                "assetClass": position.asset_class,
            }
        )
    scenario_grid = build_scenario_grid(totals)
    worst = min(scenario_grid, key=lambda item: item["pnl"]) if scenario_grid else {"pnl": 0.0}
    return {
        "totals": {key: round(value, 4) for key, value in totals.items()},
        "lines": lines,
        "scenarios": scenario_grid,
        "worstCase": worst,
        "concentration": concentration(lines),
    }


def build_scenario_grid(totals: dict[str, float]) -> list[dict[str, float]]:
    spot_shocks = [-10.0, -5.0, 0.0, 5.0, 10.0]
    vol_shocks = [-5.0, 0.0, 5.0, 10.0, 20.0]
    grid: list[dict[str, float]] = []
    base = max(abs(totals["market_value"]), 1.0)
    for spot_shift in spot_shocks:
        for vol_shift in vol_shocks:
            spot_move = spot_shift / 100.0
            pnl = (
                totals["delta"] * spot_move * base / 100.0
                + 0.5 * totals["gamma"] * (spot_move * base / 100.0) ** 2
                + totals["vega"] * vol_shift
                + totals["theta"] * 3.0
            )
            grid.append({"spotShift": spot_shift, "volShift": vol_shift, "pnl": round(pnl, 2)})
    return grid


def concentration(lines: list[dict[str, object]]) -> list[dict[str, object]]:
    if not lines:
        return []
    total_abs = sum(abs(float(line["marketValue"])) for line in lines) or 1.0
    return [
        {"symbol": line["symbol"], "weight": round(abs(float(line["marketValue"])) / total_abs, 4)}
        for line in sorted(lines, key=lambda item: abs(float(item["marketValue"])), reverse=True)[:8]
    ]


def quality_report(
    underlyings: list[UnderlyingQuote],
    options: list[OptionQuote],
    surfaces: list[SurfacePoint],
) -> dict[str, object]:
    spreads = [
        (quote.ask - quote.bid) / quote_mid(quote)
        for quote in underlyings
        if quote_mid(quote) > 0 and quote.ask >= quote.bid
    ]
    ivs = [option.implied_vol for option in options if option.implied_vol > 0]
    return {
        "underlyingCount": len(underlyings),
        "optionCount": len(options),
        "surfacePointCount": len(surfaces),
        "averageUnderlyingSpreadPct": round((mean(spreads) if spreads else 0.0) * 100.0, 3),
        "averageIv": round(mean(ivs), 4) if ivs else 0.0,
        "status": "pass" if underlyings and options and surfaces else "warn",
    }


def synthetic_expiries(now: datetime | None = None) -> list[str]:
    current = now or datetime.now(UTC)
    return [
        (current + timedelta(days=days)).date().isoformat()
        for days in (21, 45, 75, 120)
    ]
