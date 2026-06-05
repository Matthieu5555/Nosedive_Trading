"""Fixture-backed HTTP contracts for the operator frontend.

The package infra seams are still landing. These fixtures keep the BFF/web seam
stable so the UI can be built now and rewired later without changing routes.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi.encoders import jsonable_encoder

OptionType = Literal["call", "put"]
OrderSide = Literal["buy", "sell"]
OrderStatus = Literal["paper_accepted", "filled", "cancelled"]

_AS_OF = datetime(2026, 6, 5, 12, 30, tzinfo=UTC)
_TRADE_DATE = date(2026, 6, 5)
_PROVIDER = "fixture"
_CODE_VERSION = "m8-contract-fixture"
_CONFIG_HASH = "bff-contract-v1"
# Spot ladder rungs (percent): symmetric desk risk-slide grid — wide enough to
# show gamma curvature, tight enough that every rung is a plausible daily move.
# Changing it changes the ladder length and x-range of the Risk page charts.
_LADDER_SPOT_SHOCKS = [-10.0, -7.5, -5.0, -2.5, 0.0, 2.5, 5.0, 7.5, 10.0]


class UnknownUnderlyingError(ValueError):
    """Raised when an API request names an unsupported underlying."""


@dataclass(frozen=True)
class UnderlyingChoice:
    symbol: str
    name: str
    asset_class: str
    currency: str


@dataclass(frozen=True)
class Provenance:
    as_of: datetime
    provider: str
    code_version: str
    config_hash: str
    source: str
    stamp_hash: str


@dataclass(frozen=True)
class SnapshotQuote:
    symbol: str
    name: str
    last: float
    bid: float
    ask: float
    change_percent: float
    volume: int
    snapshot_ts: datetime
    currency: str


@dataclass(frozen=True)
class GreekVector:
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


@dataclass(frozen=True)
class OptionQuote:
    contract_key: str
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType
    bid: float
    ask: float
    mid: float
    implied_vol: float
    open_interest: int
    volume: int
    greeks: GreekVector


@dataclass(frozen=True)
class VolSurfacePoint:
    log_moneyness: float
    maturity_years: float
    implied_vol: float
    total_variance: float


@dataclass(frozen=True)
class VolSurfaceSlice:
    maturity_years: float
    expiry: date
    atm_vol: float
    skew_25_delta: float
    svi_a: float
    svi_b: float
    svi_rho: float
    svi_m: float
    svi_sigma: float
    rmse: float
    n_points: int


@dataclass(frozen=True)
class VolatilitySurface:
    underlying: str
    as_of: datetime
    slices: list[VolSurfaceSlice]
    points: list[VolSurfacePoint]


@dataclass(frozen=True)
class MarketDashboard:
    underlying: UnderlyingChoice
    index_snapshot: SnapshotQuote
    stock_snapshots: list[SnapshotQuote]
    option_chain: list[OptionQuote]
    greek_totals: GreekVector
    volatility_surface: VolatilitySurface
    provenance: Provenance


@dataclass(frozen=True)
class ScenarioInput:
    underlying: str
    portfolio_id: str
    spot_shock_percent: float
    vol_shock_points: float
    time_roll_days: int


@dataclass(frozen=True)
class ScenarioGridPoint:
    spot_shock_percent: float
    vol_shock_points: float
    pnl: float
    delta_after: float
    vega_after: float


@dataclass(frozen=True)
class SpotLadderPoint:
    """One rung of the spot risk ladder: PnL and greeks at a given spot shock."""

    spot_shock_percent: float
    pnl: float
    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass(frozen=True)
class ExpiryGreeks:
    """Aggregate greeks of the option chain for one expiry bucket."""

    expiry: date
    contracts: int
    greeks: GreekVector


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    requested: ScenarioInput
    baseline_value: float
    shocked_value: float
    pnl: float
    greek_before: GreekVector
    greek_after: GreekVector
    grid: list[ScenarioGridPoint]
    ladder: list[SpotLadderPoint]
    expiry_buckets: list[ExpiryGreeks]
    provenance: Provenance


@dataclass(frozen=True)
class OrderTicket:
    side: OrderSide
    symbol: str
    quantity: int
    limit_price: float
    instrument_type: Literal["index_option", "equity"]
    expiry: date | None = None
    strike: float | None = None
    option_type: OptionType | None = None
    time_in_force: Literal["day", "gtc"] = "day"


@dataclass(frozen=True)
class OrderPreview:
    ticket: OrderTicket
    estimated_notional: float
    estimated_commission: float
    risk_check: Literal["pass", "warn", "reject"]
    risk_message: str
    greek_impact: GreekVector
    paper_only: bool


@dataclass(frozen=True)
class OrderHistoryItem:
    order_id: str
    submitted_at: datetime
    ticket: OrderTicket
    status: OrderStatus
    filled_quantity: int
    average_price: float | None


@dataclass(frozen=True)
class OrdersDashboard:
    mode: Literal["paper"]
    open_orders: list[OrderHistoryItem]
    history: list[OrderHistoryItem]
    recent_preview: OrderPreview


def json_payload(value: object) -> object:
    """Return a JSON-safe representation for FastAPI responses."""

    return jsonable_encoder(value)


def list_underlyings() -> list[UnderlyingChoice]:
    """Return selectable dashboard underlyings."""

    return [
        UnderlyingChoice("SPX", "S&P 500 Index", "index", "USD"),
        UnderlyingChoice("NDX", "Nasdaq 100 Index", "index", "USD"),
        UnderlyingChoice("SX5E", "Euro Stoxx 50 Index", "index", "EUR"),
    ]


def get_market_dashboard(underlying: str = "SPX") -> MarketDashboard:
    """Return the current operator dashboard for one underlying."""

    choice = _resolve_underlying(underlying)
    scale = _scale_for(choice.symbol)
    index = SnapshotQuote(
        symbol=choice.symbol,
        name=choice.name,
        last=round(5312.42 * scale, 2),
        bid=round(5311.9 * scale, 2),
        ask=round(5312.8 * scale, 2),
        change_percent=0.42 if choice.symbol != "SX5E" else -0.18,
        volume=int(1_840_000 * scale),
        snapshot_ts=_AS_OF,
        currency=choice.currency,
    )
    stocks = _stock_snapshots(choice.symbol, choice.currency, scale)
    options = _option_chain(choice.symbol, scale)
    greek_totals = _sum_greeks(options)
    surface = _vol_surface(choice.symbol)
    return MarketDashboard(
        underlying=choice,
        index_snapshot=index,
        stock_snapshots=stocks,
        option_chain=options,
        greek_totals=greek_totals,
        volatility_surface=surface,
        provenance=_provenance(f"market:{choice.symbol}"),
    )


def get_scenario_baseline(underlying: str = "SPX") -> ScenarioResult:
    """Return a neutral scenario result for initial page rendering."""

    request = ScenarioInput(
        underlying=_resolve_underlying(underlying).symbol,
        portfolio_id="CORE-INDEX-OPTIONS",
        spot_shock_percent=0.0,
        vol_shock_points=0.0,
        time_roll_days=0,
    )
    return run_scenario(request)


def run_scenario(request: ScenarioInput) -> ScenarioResult:
    """Run a deterministic paper scenario over the fixture portfolio."""

    choice = _resolve_underlying(request.underlying)
    dashboard = get_market_dashboard(choice.symbol)
    baseline = _portfolio_value(dashboard.option_chain)
    greek_before = dashboard.greek_totals
    pnl = _scenario_pnl(
        baseline,
        greek_before,
        request.spot_shock_percent,
        request.vol_shock_points,
        request.time_roll_days,
    )
    greek_after = _shocked_greeks(
        greek_before,
        request.spot_shock_percent,
        request.vol_shock_points,
        request.time_roll_days,
    )
    scenario_id = _stable_id(
        f"{choice.symbol}:{request.portfolio_id}:{request.spot_shock_percent}:"
        f"{request.vol_shock_points}:{request.time_roll_days}"
    )
    return ScenarioResult(
        scenario_id=scenario_id,
        requested=replace(request, underlying=choice.symbol),
        baseline_value=round(baseline, 2),
        shocked_value=round(baseline + pnl, 2),
        pnl=round(pnl, 2),
        greek_before=greek_before,
        greek_after=greek_after,
        grid=_scenario_grid(baseline, greek_before),
        ladder=_spot_ladder(
            baseline, greek_before, request.vol_shock_points, request.time_roll_days
        ),
        expiry_buckets=_expiry_buckets(dashboard.option_chain),
        provenance=_provenance(f"scenario:{scenario_id}"),
    )


def get_orders_dashboard() -> OrdersDashboard:
    """Return paper-order state for the order page."""

    preview = preview_order(
        OrderTicket(
            side="buy",
            symbol="SPX",
            quantity=2,
            limit_price=47.5,
            instrument_type="index_option",
            expiry=date(2026, 6, 19),
            strike=5350.0,
            option_type="call",
        )
    )
    open_order = OrderHistoryItem(
        order_id="PAPER-8F31",
        submitted_at=_AS_OF - timedelta(minutes=11),
        ticket=preview.ticket,
        status="paper_accepted",
        filled_quantity=0,
        average_price=None,
    )
    filled_order = OrderHistoryItem(
        order_id="PAPER-6B12",
        submitted_at=_AS_OF - timedelta(hours=2, minutes=14),
        ticket=OrderTicket(
            side="sell",
            symbol="SPX",
            quantity=1,
            limit_price=32.2,
            instrument_type="index_option",
            expiry=date(2026, 6, 19),
            strike=5250.0,
            option_type="put",
        ),
        status="filled",
        filled_quantity=1,
        average_price=32.4,
    )
    return OrdersDashboard(
        mode="paper",
        open_orders=[open_order],
        history=[open_order, filled_order],
        recent_preview=preview,
    )


def preview_order(ticket: OrderTicket) -> OrderPreview:
    """Return a deterministic pre-trade risk preview for a paper ticket."""

    notional = abs(ticket.quantity * ticket.limit_price * _contract_multiplier(ticket))
    commission = round(max(1.0, abs(ticket.quantity) * 0.65), 2)
    impact = _greek_impact(ticket)
    risk_check: Literal["pass", "warn", "reject"] = "pass"
    message = "within paper limits"
    if notional > 150_000:
        risk_check = "warn"
        message = "large notional for paper limit"
    if ticket.quantity <= 0 or ticket.limit_price <= 0:
        risk_check = "reject"
        message = "quantity and limit price must be positive"
    return OrderPreview(
        ticket=ticket,
        estimated_notional=round(notional, 2),
        estimated_commission=commission,
        risk_check=risk_check,
        risk_message=message,
        greek_impact=impact,
        paper_only=True,
    )


def submit_paper_order(ticket: OrderTicket) -> OrderHistoryItem:
    """Accept a paper order after the same preview validation used by the UI."""

    preview = preview_order(ticket)
    if preview.risk_check == "reject":
        raise ValueError(preview.risk_message)
    return OrderHistoryItem(
        order_id=f"PAPER-{_stable_id(str(ticket))[:4].upper()}",
        submitted_at=_AS_OF,
        ticket=ticket,
        status="paper_accepted",
        filled_quantity=0,
        average_price=None,
    )


def _resolve_underlying(symbol: str) -> UnderlyingChoice:
    normalized = symbol.upper()
    for choice in list_underlyings():
        if choice.symbol == normalized:
            return choice
    raise UnknownUnderlyingError(f"Unknown underlying: {symbol}")


def _scale_for(symbol: str) -> float:
    return {"SPX": 1.0, "NDX": 3.54, "SX5E": 0.95}[symbol]


def _stock_snapshots(underlying: str, currency: str, scale: float) -> list[SnapshotQuote]:
    raw = [
        ("AAPL", "Apple", 196.45, 0.58, 46_210_000),
        ("MSFT", "Microsoft", 472.11, 0.31, 22_405_000),
        ("NVDA", "NVIDIA", 141.72, 1.82, 198_332_000),
        ("AMZN", "Amazon", 186.24, -0.22, 39_640_000),
        ("JPM", "JPMorgan", 267.15, -0.41, 9_880_000),
        ("XOM", "Exxon Mobil", 109.88, 0.17, 13_420_000),
    ]
    if underlying == "SX5E":
        raw = [
            ("ASML", "ASML", 738.4, -0.35, 1_420_000),
            ("SAP", "SAP", 286.1, 0.21, 2_100_000),
            ("MC", "LVMH", 476.8, -0.82, 810_000),
            ("SAN", "Sanofi", 85.3, 0.18, 3_400_000),
            ("TTE", "TotalEnergies", 54.7, 0.39, 4_920_000),
            ("AIR", "Airbus", 174.2, -0.16, 1_280_000),
        ]
    elif underlying == "NDX":
        raw = [
            ("NVDA", "NVIDIA", 141.72, 1.82, 198_332_000),
            ("MSFT", "Microsoft", 472.11, 0.31, 22_405_000),
            ("AAPL", "Apple", 196.45, 0.58, 46_210_000),
            ("AVGO", "Broadcom", 248.33, 1.1, 15_760_000),
            ("META", "Meta", 641.22, -0.44, 11_980_000),
            ("TSLA", "Tesla", 178.04, -1.38, 74_600_000),
        ]
    return [
        SnapshotQuote(
            symbol=symbol,
            name=name,
            last=round(last * (1.0 if currency == "USD" else 0.92), 2),
            bid=round(last * (1.0 if currency == "USD" else 0.92) - 0.03 * scale, 2),
            ask=round(last * (1.0 if currency == "USD" else 0.92) + 0.04 * scale, 2),
            change_percent=change,
            volume=volume,
            snapshot_ts=_AS_OF - timedelta(seconds=len(symbol) * 3),
            currency=currency,
        )
        for symbol, name, last, change, volume in raw
    ]


def _option_chain(underlying: str, scale: float) -> list[OptionQuote]:
    expiries = [date(2026, 6, 19), date(2026, 7, 17), date(2026, 9, 18)]
    strikes = [5150.0, 5250.0, 5350.0, 5450.0]
    quotes: list[OptionQuote] = []
    for expiry_index, expiry in enumerate(expiries):
        maturity = (expiry - _TRADE_DATE).days / 365
        for strike in strikes:
            moneyness = (strike / 5312.42) - 1.0
            for option_type in ("call", "put"):
                skew = 0.02 if option_type == "put" and strike < 5312.42 else 0.0
                implied_vol = round(0.165 + expiry_index * 0.012 + abs(moneyness) * 0.16 + skew, 4)
                if option_type == "call":
                    intrinsic = max(5312.42 - strike, 0)
                else:
                    intrinsic = max(strike - 5312.42, 0)
                time_value = 5312.42 * implied_vol * math.sqrt(max(maturity, 0.01)) * 0.08
                mid = round((intrinsic + time_value) * scale / 10, 2)
                spread = max(0.15, mid * 0.018)
                delta = _delta(option_type, moneyness, expiry_index)
                quote = OptionQuote(
                    contract_key=f"{underlying}|{expiry.isoformat()}|{int(strike)}|{option_type.upper()}",
                    underlying=underlying,
                    expiry=expiry,
                    strike=round(strike * scale, 2),
                    option_type=option_type,
                    bid=round(max(mid - spread / 2, 0.01), 2),
                    ask=round(mid + spread / 2, 2),
                    mid=mid,
                    implied_vol=implied_vol,
                    open_interest=int(6_000 + (strike - 5100) * 4 + expiry_index * 900),
                    volume=int(400 + abs(strike - 5312) * 1.7 + expiry_index * 110),
                    greeks=GreekVector(
                        delta=delta,
                        gamma=round(0.0032 - abs(moneyness) * 0.001, 5),
                        vega=round(7.5 + expiry_index * 2.1 - abs(moneyness) * 5.0, 4),
                        theta=round(-1.4 - expiry_index * 0.35 - implied_vol, 4),
                        rho=round(
                            (0.8 + expiry_index * 0.25)
                            * (1 if option_type == "call" else -1),
                            4,
                        ),
                    ),
                )
                quotes.append(quote)
    return quotes


def _delta(option_type: str, moneyness: float, expiry_index: int) -> float:
    base = 0.52 - moneyness * 2.6 + expiry_index * 0.02
    if option_type == "call":
        return round(min(max(base, 0.05), 0.95), 4)
    return round(-min(max(1.0 - base, 0.05), 0.95), 4)


def _sum_greeks(options: list[OptionQuote]) -> GreekVector:
    return GreekVector(
        delta=round(sum(o.greeks.delta for o in options), 4),
        gamma=round(sum(o.greeks.gamma for o in options), 5),
        vega=round(sum(o.greeks.vega for o in options), 4),
        theta=round(sum(o.greeks.theta for o in options), 4),
        rho=round(sum(o.greeks.rho for o in options), 4),
    )


def _vol_surface(underlying: str) -> VolatilitySurface:
    maturities = [0.04, 0.12, 0.29, 0.54]
    log_moneyness_values = [-0.18, -0.09, 0.0, 0.09, 0.18]
    points: list[VolSurfacePoint] = []
    slices: list[VolSurfaceSlice] = []
    for index, maturity in enumerate(maturities):
        atm = 0.165 + index * 0.018
        for log_moneyness in log_moneyness_values:
            vol = atm + abs(log_moneyness) * 0.22 - log_moneyness * 0.045
            points.append(
                VolSurfacePoint(
                    log_moneyness=log_moneyness,
                    maturity_years=maturity,
                    implied_vol=round(vol, 4),
                    total_variance=round(vol * vol * maturity, 6),
                )
            )
        slices.append(
            VolSurfaceSlice(
                maturity_years=maturity,
                expiry=_TRADE_DATE + timedelta(days=round(maturity * 365)),
                atm_vol=round(atm, 4),
                skew_25_delta=round(-0.045 - index * 0.008, 4),
                svi_a=round(0.010 + index * 0.004, 5),
                svi_b=round(0.125 + index * 0.015, 5),
                svi_rho=round(-0.42 + index * 0.03, 5),
                svi_m=round(-0.015 + index * 0.004, 5),
                svi_sigma=round(0.18 + index * 0.018, 5),
                rmse=round(0.0019 + index * 0.0007, 5),
                n_points=24 + index * 5,
            )
        )
    return VolatilitySurface(underlying=underlying, as_of=_AS_OF, slices=slices, points=points)


def _portfolio_value(options: list[OptionQuote]) -> float:
    selected = [quote for quote in options if quote.strike in {5250.0, 5350.0, 5450.0}]
    if not selected:
        selected = options[:6]
    return sum(quote.mid * 100 for quote in selected)


# One fixture model for every scenario view (headline PnL, spot x vol grid,
# spot ladder): first-order delta/vega/theta terms plus a second-order gamma
# term so the ladder shows the curvature an options book actually has. The
# 0.0025 / 0.85 / 0.18 factors set fixture-scale magnitudes only.
def _scenario_pnl(
    baseline: float, greek: GreekVector, spot: float, vol: float, days: int
) -> float:
    spot_effect = baseline * (spot * greek.delta + 0.5 * spot * spot * greek.gamma) * 0.0025
    vol_effect = vol * greek.vega * 0.85
    time_effect = days * greek.theta * 0.18
    return spot_effect + vol_effect + time_effect


def _shocked_greeks(greek: GreekVector, spot: float, vol: float, days: int) -> GreekVector:
    # Gamma, vega, and theta magnitudes shrink as the book moves away from
    # the money; delta rides the gamma, vega rides the vol shock.
    distance_from_money = abs(spot)
    return GreekVector(
        delta=round(greek.delta + spot * greek.gamma * 0.8, 4),
        gamma=round(max(greek.gamma * (1.0 - distance_from_money * 0.01), 0.0), 5),
        vega=round(max((greek.vega + vol * 0.12) * (1.0 - distance_from_money * 0.012), 0.0), 4),
        theta=round((greek.theta - days * 0.03) * (1.0 - distance_from_money * 0.008), 4),
        rho=round(greek.rho + spot * 0.02, 4),
    )


def _spot_ladder(
    baseline: float, greek: GreekVector, vol: float, days: int
) -> list[SpotLadderPoint]:
    points: list[SpotLadderPoint] = []
    for spot in _LADDER_SPOT_SHOCKS:
        shocked = _shocked_greeks(greek, spot, vol, days)
        points.append(
            SpotLadderPoint(
                spot_shock_percent=spot,
                pnl=round(_scenario_pnl(baseline, greek, spot, vol, days), 2),
                delta=shocked.delta,
                gamma=shocked.gamma,
                vega=shocked.vega,
                theta=shocked.theta,
            )
        )
    return points


def _expiry_buckets(options: list[OptionQuote]) -> list[ExpiryGreeks]:
    grouped: dict[date, list[OptionQuote]] = {}
    for quote in options:
        grouped.setdefault(quote.expiry, []).append(quote)
    return [
        ExpiryGreeks(expiry=expiry, contracts=len(quotes), greeks=_sum_greeks(quotes))
        for expiry, quotes in sorted(grouped.items())
    ]


def _scenario_grid(baseline: float, greek: GreekVector) -> list[ScenarioGridPoint]:
    points: list[ScenarioGridPoint] = []
    for spot in [-5.0, -2.5, 0.0, 2.5, 5.0]:
        for vol in [-4.0, 0.0, 4.0]:
            shocked = _shocked_greeks(greek, spot, vol, 0)
            points.append(
                ScenarioGridPoint(
                    spot_shock_percent=spot,
                    vol_shock_points=vol,
                    pnl=round(_scenario_pnl(baseline, greek, spot, vol, 0), 2),
                    delta_after=shocked.delta,
                    vega_after=shocked.vega,
                )
            )
    return points


def _greek_impact(ticket: OrderTicket) -> GreekVector:
    sign = 1 if ticket.side == "buy" else -1
    quantity = sign * ticket.quantity
    if ticket.instrument_type == "equity":
        return GreekVector(
            delta=round(quantity, 4),
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            rho=0.0,
        )
    delta = 0.48 if ticket.option_type == "call" else -0.42
    return GreekVector(
        delta=round(quantity * delta, 4),
        gamma=round(quantity * 0.003, 5),
        vega=round(quantity * 8.2, 4),
        theta=round(quantity * -1.35, 4),
        rho=round(quantity * (0.72 if ticket.option_type == "call" else -0.54), 4),
    )


def _contract_multiplier(ticket: OrderTicket) -> int:
    return 100 if ticket.instrument_type == "index_option" else 1


def _provenance(source: str) -> Provenance:
    stamp_hash = _stable_id(f"{source}:{_AS_OF.isoformat()}:{_CONFIG_HASH}")
    return Provenance(
        as_of=_AS_OF,
        provider=_PROVIDER,
        code_version=_CODE_VERSION,
        config_hash=_CONFIG_HASH,
        source=source,
        stamp_hash=stamp_hash,
    )


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
