from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import sin
from random import Random
from typing import Protocol

from analytics import (
    OptionQuote,
    Position,
    UnderlyingQuote,
    black_scholes_price,
    greeks,
    quote_mid,
    synthetic_expiries,
    years_to_expiry,
)


@dataclass(frozen=True)
class MarketSnapshot:
    mode: str
    timestamp: str
    underlyings: list[UnderlyingQuote]
    options: list[OptionQuote]
    positions: list[Position]
    diagnostics: dict[str, object]

    def as_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "timestamp": self.timestamp,
            "underlyings": [asdict(item) for item in self.underlyings],
            "options": [asdict(item) for item in self.options],
            "positions": [asdict(item) for item in self.positions],
            "diagnostics": self.diagnostics,
        }


class MarketDataProvider(Protocol):
    def snapshot(self, symbols: list[str]) -> MarketSnapshot:
        ...

    def preview_order(self, ticket: dict[str, object]) -> dict[str, object]:
        ...

    def place_order(self, ticket: dict[str, object]) -> dict[str, object]:
        ...


class DemoProvider:
    def __init__(self, seed: int = 1780037915) -> None:
        self._seed = seed

    def snapshot(self, symbols: list[str]) -> MarketSnapshot:
        requested = symbols or ["SPY", "QQQ", "IWM", "DIA", "VIX"]
        now = datetime.now(UTC).replace(microsecond=0)
        rng = Random(self._seed + now.minute)
        underlyings: list[UnderlyingQuote] = []
        options: list[OptionQuote] = []
        for index, symbol in enumerate(requested):
            base = self._base_price(symbol)
            drift = sin(now.minute / 60.0 + index) * base * 0.004
            last = base + drift + rng.uniform(-0.35, 0.35)
            spread = max(0.01, last * (0.0007 + index * 0.00015))
            quote = UnderlyingQuote(
                symbol=symbol,
                name=self._name(symbol),
                asset_class="index" if symbol in {"VIX", "SPX", "NDX", "RUT"} else "equity",
                bid=round(last - spread / 2.0, 2),
                ask=round(last + spread / 2.0, 2),
                last=round(last, 2),
                volume=500_000 + index * 125_000 + rng.randint(0, 35_000),
                timestamp=now.isoformat(),
            )
            underlyings.append(quote)
            if symbol == "VIX":
                continue
            options.extend(self._options_for(quote, rng, now))
        positions = self._positions(underlyings, options)
        return MarketSnapshot(
            mode="demo",
            timestamp=now.isoformat(),
            underlyings=underlyings,
            options=options,
            positions=positions,
            diagnostics={
                "provider": "deterministic demo",
                "message": "Configure IBKR_MODE=ibkr to read paper trading market data.",
                "readOnlyOrders": True,
            },
        )

    def preview_order(self, ticket: dict[str, object]) -> dict[str, object]:
        return {
            "status": "preview",
            "provider": "demo",
            "estimatedCommission": 1.0,
            "marginImpact": 0.0,
            "warnings": ["Demo mode: no order can reach a broker."],
            "ticket": ticket,
        }

    def place_order(self, ticket: dict[str, object]) -> dict[str, object]:
        return {
            "status": "blocked",
            "provider": "demo",
            "message": "Order placement is disabled in demo mode.",
            "ticket": ticket,
        }

    def _base_price(self, symbol: str) -> float:
        prices = {"SPY": 532.0, "QQQ": 456.0, "IWM": 205.0, "DIA": 389.0, "VIX": 14.7}
        return prices.get(symbol.upper(), 100.0 + (sum(ord(char) for char in symbol) % 170))

    def _name(self, symbol: str) -> str:
        names = {
            "SPY": "S&P 500 ETF",
            "QQQ": "Nasdaq 100 ETF",
            "IWM": "Russell 2000 ETF",
            "DIA": "Dow Jones ETF",
            "VIX": "CBOE Volatility Index",
        }
        return names.get(symbol.upper(), f"{symbol.upper()} underlying")

    def _options_for(self, quote: UnderlyingQuote, rng: Random, now: datetime) -> list[OptionQuote]:
        spot = quote_mid(quote)
        expiries = synthetic_expiries(now)
        strikes = [round(spot * factor / 5.0) * 5.0 for factor in (0.88, 0.94, 1.0, 1.06, 1.12)]
        result: list[OptionQuote] = []
        for expiry_index, expiry in enumerate(expiries):
            tau = years_to_expiry(expiry, now)
            for strike in strikes:
                skew = max((strike / spot) - 1.0, -0.4)
                base_iv = 0.16 + 0.025 * expiry_index + 0.18 * abs(skew)
                for right in ("C", "P"):
                    iv = max(0.06, base_iv + (0.015 if right == "P" and strike < spot else 0.0))
                    model = black_scholes_price(spot, strike, tau, iv, right)
                    spread = max(0.03, model * 0.035)
                    greek = greeks(spot, strike, tau, iv, right)
                    result.append(
                        OptionQuote(
                            underlying=quote.symbol,
                            expiry=expiry,
                            strike=strike,
                            right=right,
                            bid=round(max(model - spread / 2.0, 0.01), 2),
                            ask=round(model + spread / 2.0, 2),
                            last=round(model + rng.uniform(-spread / 3.0, spread / 3.0), 2),
                            implied_vol=round(iv, 4),
                            open_interest=500 + int(abs(strike - spot) * 9) + rng.randint(0, 300),
                            delta=round(greek["delta"], 4),
                            gamma=round(greek["gamma"], 6),
                            vega=round(greek["vega"], 4),
                            theta=round(greek["theta"], 4),
                        )
                    )
        return result

    def _positions(self, underlyings: list[UnderlyingQuote], options: list[OptionQuote]) -> list[Position]:
        positions: list[Position] = []
        for quote in underlyings[:3]:
            positions.append(
                Position(
                    symbol=quote.symbol,
                    quantity={"SPY": 40, "QQQ": -25, "IWM": 65}.get(quote.symbol, 10),
                    average_cost=round(quote.last * 0.98, 2),
                    market_price=quote.last,
                    asset_class=quote.asset_class,
                )
            )
        for option in options[::17][:6]:
            symbol = f"{option.underlying}-{option.expiry}-{option.strike:.0f}-{option.right}"
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=1 if option.right == "C" else -1,
                    average_cost=option_mid_for_position(option),
                    market_price=option_mid_for_position(option),
                    asset_class="option",
                )
            )
        return positions


def option_mid_for_position(option: OptionQuote) -> float:
    return round((option.bid + option.ask) / 2.0, 2)


class IbkrProvider:
    def __init__(self, host: str, port: int, client_id: int, enable_orders: bool) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.enable_orders = enable_orders

    def snapshot(self, symbols: list[str]) -> MarketSnapshot:
        try:
            from ib_insync import IB, Index, Stock  # type: ignore
        except ImportError as exc:
            raise RuntimeError("ib_insync is required for IBKR_MODE=ibkr. Run: uv sync --extra ibkr") from exc

        ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id, readonly=not self.enable_orders)
        try:
            requested = symbols or ["SPY", "QQQ", "IWM", "DIA", "VIX"]
            contracts = [
                Index(symbol, "CBOE", "USD") if symbol.upper() == "VIX" else Stock(symbol, "SMART", "USD")
                for symbol in requested
            ]
            ib.qualifyContracts(*contracts)
            tickers = ib.reqTickers(*contracts)
            now = datetime.now(UTC).replace(microsecond=0)
            underlyings = []
            for contract, ticker in zip(contracts, tickers, strict=False):
                last = float(ticker.marketPrice() or ticker.last or ticker.close or 0.0)
                bid = float(ticker.bid or last)
                ask = float(ticker.ask or last)
                underlyings.append(
                    UnderlyingQuote(
                        symbol=contract.symbol,
                        name=contract.symbol,
                        asset_class="index" if contract.secType == "IND" else "equity",
                        bid=round(bid, 4),
                        ask=round(ask, 4),
                        last=round(last, 4),
                        volume=int(ticker.volume or 0),
                        timestamp=now.isoformat(),
                    )
                )
            positions = [
                Position(
                    symbol=position.contract.localSymbol or position.contract.symbol,
                    quantity=int(position.position),
                    average_cost=float(position.avgCost),
                    market_price=0.0,
                    asset_class=position.contract.secType.lower(),
                )
                for position in ib.positions()
            ]
            return MarketSnapshot(
                mode="ibkr",
                timestamp=now.isoformat(),
                underlyings=underlyings,
                options=[],
                positions=positions,
                diagnostics={
                    "provider": "IBKR paper via ib_insync",
                    "host": self.host,
                    "port": self.port,
                    "clientId": self.client_id,
                    "options": "Option-chain enrichment is intentionally manual-gated for pacing safety.",
                    "ordersEnabled": self.enable_orders,
                },
            )
        finally:
            ib.disconnect()

    def preview_order(self, ticket: dict[str, object]) -> dict[str, object]:
        return {
            "status": "preview",
            "provider": "ibkr",
            "warnings": [
                "Preview only. Set IBKR_ENABLE_ORDERS=true and use TWS/Gateway paper account to transmit."
            ],
            "ticket": ticket,
        }

    def place_order(self, ticket: dict[str, object]) -> dict[str, object]:
        if not self.enable_orders:
            return {
                "status": "blocked",
                "provider": "ibkr",
                "message": "IBKR_ENABLE_ORDERS is false; order was not transmitted.",
                "ticket": ticket,
            }
        return {
            "status": "blocked",
            "provider": "ibkr",
            "message": "Transmission hook is deliberately blocked until contract qualification is completed.",
            "ticket": ticket,
        }
