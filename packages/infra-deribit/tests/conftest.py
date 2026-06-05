"""Shared fixtures for infra-deribit tests — all synthetic, no network required."""

from __future__ import annotations

import pytest


@pytest.fixture()
def sample_instruments_response() -> list[dict]:
    """Minimal /public/get_instruments payload for BTC options."""
    return [
        {
            "instrument_name": "BTC-27JUN25-100000-C",
            "kind": "option",
            "option_type": "call",
            "strike": 100000.0,
            "expiration_timestamp": 1751040000000,
        },
        {
            "instrument_name": "BTC-27JUN25-100000-P",
            "kind": "option",
            "option_type": "put",
            "strike": 100000.0,
            "expiration_timestamp": 1751040000000,
        },
        {
            "instrument_name": "BTC-27JUN25-80000-C",
            "kind": "option",
            "option_type": "call",
            "strike": 80000.0,
            "expiration_timestamp": 1751040000000,
        },
    ]


@pytest.fixture()
def sample_ticker_ws_frame() -> dict:
    """Minimal Deribit WebSocket ticker notification frame (real field names; BTC-quoted)."""
    return {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {
            "channel": "ticker.BTC-27JUN25-100000-C.100ms",
            "data": {
                "instrument_name": "BTC-27JUN25-100000-C",
                "best_bid_price": 0.05,  # in BTC
                "best_ask_price": 0.06,
                "last_price": 0.055,
                "mark_price": 0.052,
                "mark_iv": 65.3,  # Deribit sends as percentage: 65.3% = 0.653
                "index_price": 67000.0,  # USD/BTC, used to convert option prices
                "underlying_price": 67000.0,  # USD spot of the underlying
            },
        },
    }


@pytest.fixture()
def sample_ticker_ws_frame_no_mark_iv() -> dict:
    """Ticker frame with no mark_iv field (non-crypto feed compatibility)."""
    return {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {
            "channel": "ticker.BTC-27JUN25-100000-C.100ms",
            "data": {
                "instrument_name": "BTC-27JUN25-100000-C",
                "best_bid_price": 0.05,
                "best_ask_price": 0.06,
                "last_price": 0.055,
                "mark_price": 0.052,
                "index_price": 67000.0,
                "underlying_price": 67000.0,
            },
        },
    }
