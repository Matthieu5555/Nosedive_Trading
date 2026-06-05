"""Shared fixtures for infra-saxo tests — all synthetic, no network required."""

from __future__ import annotations

import pytest


@pytest.fixture()
def sample_instruments_response() -> dict:
    """Minimal /ref/v1/instruments response for SPY EtfOption lookup."""
    return {
        "Data": [
            {
                "Identifier": 9999,
                "Symbol": "SPY",
                "AssetType": "EtfOption",
                "OptionRootId": 12345,
                "CurrencyCode": "USD",
                "ExchangeId": "OPRA",
            }
        ]
    }


@pytest.fixture()
def sample_option_space_response() -> dict:
    """Minimal /ref/v1/instruments/contractoptionspaces response."""
    return {
        "OptionSpace": [
            {
                "Expiry": "2025-06-27T00:00:00Z",
                "DisplayExpiry": "2025-06-27",
                "SpecificOptions": [
                    {
                        "PutCall": "Call",
                        "StrikePrice": 530.0,
                        "Uic": 11111111,
                        "UnderlyingUic": 9999,
                    },
                    {
                        "PutCall": "Put",
                        "StrikePrice": 530.0,
                        "Uic": 11111112,
                        "UnderlyingUic": 9999,
                    },
                    {
                        "PutCall": "Call",
                        "StrikePrice": 535.0,
                        "Uic": 11111113,
                        "UnderlyingUic": 9999,
                    },
                    {
                        "PutCall": "Put",
                        "StrikePrice": 535.0,
                        "Uic": 11111114,
                        "UnderlyingUic": 9999,
                    },
                ],
            }
        ]
    }


@pytest.fixture()
def sample_ws_strike_frame() -> dict:
    """One Saxo options-chain WebSocket strike payload (snapshot format)."""
    return {
        "Strike": 530.0,
        "Call": {
            "Bid": 6.30,
            "Ask": 6.50,
            "Greeks": {
                "Delta": 0.55,
                "Gamma": 0.04,
                "Vega": 0.21,
                "Theta": -0.08,
                "MidVolatility": 0.185,
            },
        },
        "Put": {
            "Bid": 5.80,
            "Ask": 6.00,
            "Greeks": {
                "Delta": -0.45,
                "Gamma": 0.04,
                "Vega": 0.21,
                "Theta": -0.07,
                "MidVolatility": 0.185,
            },
        },
    }


@pytest.fixture()
def sample_ws_strike_frame_no_iv() -> dict:
    """Strike frame whose sides carry no Greeks.MidVolatility (partial data, no mark_iv)."""
    return {
        "Strike": 530.0,
        "Call": {"Bid": 6.30, "Ask": 6.50},
        "Put": {"Bid": 5.80, "Ask": 6.00},
    }
