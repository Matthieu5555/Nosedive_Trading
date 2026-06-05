"""Tests for Deribit instrument discovery — pure functions, no network."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from algotrading.infra.universe.contracts import Right, instrument_key, parse_instrument_key
from algotrading.infra_deribit.collectors.deribit_discovery import (
    discover_instruments,
    parse_deribit_instrument_name,
)


class TestParseDeribitInstrumentName:
    def test_btc_call(self):
        contract = parse_deribit_instrument_name("BTC-25JUL25-100000-C")
        assert contract.symbol == "BTC"
        assert contract.expiry == date(2025, 7, 25)
        assert contract.strike == Decimal("100000")
        assert contract.right == Right.CALL
        assert contract.multiplier == 1
        assert contract.exchange == "DERIBIT"
        assert contract.currency == "USD"
        assert contract.security_type == "OPT"

    def test_eth_put(self):
        contract = parse_deribit_instrument_name("ETH-26DEC25-3000-P")
        assert contract.symbol == "ETH"
        assert contract.expiry == date(2025, 12, 26)
        assert contract.strike == Decimal("3000")
        assert contract.right == Right.PUT

    def test_round_trip_instrument_key(self):
        contract = parse_deribit_instrument_name("BTC-27JUN25-80000-C")
        key = instrument_key(contract)
        rebuilt = parse_instrument_key(key)
        assert rebuilt == contract

    def test_malformed_missing_parts(self):
        with pytest.raises(ValueError, match="4 dash-separated"):
            parse_deribit_instrument_name("BTC-25JUL25-100000")

    def test_malformed_bad_expiry(self):
        with pytest.raises(ValueError, match="cannot parse expiry"):
            parse_deribit_instrument_name("BTC-BADDATE-100000-C")

    def test_malformed_bad_strike(self):
        with pytest.raises(ValueError, match="cannot parse strike"):
            parse_deribit_instrument_name("BTC-25JUL25-NOTSTRIKE-C")

    def test_malformed_bad_right(self):
        with pytest.raises((ValueError, KeyError)):
            parse_deribit_instrument_name("BTC-25JUL25-100000-X")


class TestDiscoverInstruments:
    def test_returns_contracts_within_window(self, sample_instruments_response):
        class _MockTransport:
            def get(self, path, params=None):
                return sample_instruments_response

        # Use a window that includes past dates so fixtures with Jun 2025 expiry always pass.
        contracts = discover_instruments(_MockTransport(), "BTC", min_days=-9999, max_days=9999)
        assert len(contracts) == 3
        symbols = {c.symbol for c in contracts}
        assert symbols == {"BTC"}

    def test_filters_by_maturity_window(self, sample_instruments_response):
        class _MockTransport:
            def get(self, path, params=None):
                return sample_instruments_response

        # min_days=99999 excludes everything.
        contracts = discover_instruments(_MockTransport(), "BTC", min_days=99999, max_days=999999)
        assert contracts == []

    def test_skips_unparseable_instruments(self):
        class _MockTransport:
            def get(self, path, params=None):
                return [
                    {"instrument_name": "BTC-BADDATE-100000-C"},
                    {"instrument_name": "BTC-27JUN25-100000-C"},
                ]

        contracts = discover_instruments(_MockTransport(), "BTC", min_days=-9999, max_days=9999)
        assert len(contracts) == 1

    def test_empty_response(self):
        class _MockTransport:
            def get(self, path, params=None):
                return []

        contracts = discover_instruments(_MockTransport(), "BTC", min_days=0, max_days=9999)
        assert contracts == []
