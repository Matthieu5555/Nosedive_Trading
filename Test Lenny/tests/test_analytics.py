from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from analytics import black_scholes_price, greeks  # noqa: E402
from server import normalize_ticket  # noqa: E402


class AnalyticsTests(unittest.TestCase):
    def test_atm_call_price_is_positive_and_below_spot(self) -> None:
        price = black_scholes_price(spot=100.0, strike=100.0, maturity_years=0.5, volatility=0.2, right="C")
        self.assertGreater(price, 0.0)
        self.assertLess(price, 100.0)

    def test_call_delta_is_between_zero_and_one(self) -> None:
        result = greeks(spot=100.0, strike=100.0, maturity_years=0.5, volatility=0.2, right="C")
        self.assertGreater(result["delta"], 0.0)
        self.assertLess(result["delta"], 1.0)
        self.assertGreater(result["gamma"], 0.0)
        self.assertGreater(result["vega"], 0.0)

    def test_limit_order_requires_limit_price(self) -> None:
        with self.assertRaises(ValueError):
            normalize_ticket({"symbol": "SPY", "action": "BUY", "quantity": 1, "orderType": "LMT"})

    def test_market_order_normalizes_symbol(self) -> None:
        ticket = normalize_ticket({"symbol": " spy ", "action": "buy", "quantity": 2, "orderType": "MKT"})
        self.assertEqual(ticket.symbol, "SPY")
        self.assertEqual(ticket.action, "BUY")
        self.assertEqual(ticket.quantity, 2)


if __name__ == "__main__":
    unittest.main()
