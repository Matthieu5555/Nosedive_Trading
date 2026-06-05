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


class TestDiscoverInstrumentsAsOf:
    """The maturity filter is measured from an injected ``as_of``, never the wall clock.

    All three fixtures (``sample_instruments_response``) expire 2025-06-27, so the selected
    universe is a pure function of ``as_of`` + the window. Expected day-counts are derived by
    hand from the calendar, independent of the implementation.
    """

    @staticmethod
    def _transport(payload):
        class _MockTransport:
            def get(self, path, params=None):
                return payload

        return _MockTransport()

    def test_as_of_selects_full_window(self, sample_instruments_response):
        # 2025-06-01 → 2025-06-27 is 26 days; inside the default [1, 180] window → all 3.
        contracts = discover_instruments(
            self._transport(sample_instruments_response), "BTC", as_of=date(2025, 6, 1)
        )
        assert len(contracts) == 3

    def test_as_of_at_lower_boundary_includes(self, sample_instruments_response):
        # 2025-06-26 → expiry is 1 day out; min_days=1 includes it.
        contracts = discover_instruments(
            self._transport(sample_instruments_response), "BTC", as_of=date(2025, 6, 26)
        )
        assert len(contracts) == 3

    def test_as_of_on_expiry_excludes(self, sample_instruments_response):
        # 2025-06-27 → 0 days to expiry; below min_days=1 → empty universe.
        contracts = discover_instruments(
            self._transport(sample_instruments_response), "BTC", as_of=date(2025, 6, 27)
        )
        assert contracts == []

    def test_as_of_beyond_max_window_excludes(self, sample_instruments_response):
        # 2024-12-01 → 2025-06-27 is 208 days; beyond the default max_days=180 → empty.
        contracts = discover_instruments(
            self._transport(sample_instruments_response), "BTC", as_of=date(2024, 12, 1)
        )
        assert contracts == []

    def test_selection_is_deterministic_for_replay(self, sample_instruments_response):
        # Same payload + same as_of must reselect the identical universe, regardless of when
        # the run happens — the property a replay relies on. Compare by instrument key.
        as_of = date(2025, 6, 10)
        first = discover_instruments(
            self._transport(sample_instruments_response), "BTC", as_of=as_of
        )
        second = discover_instruments(
            self._transport(sample_instruments_response), "BTC", as_of=as_of
        )
        assert [c.symbol for c in first] == [c.symbol for c in second]
        assert [(c.expiry, c.strike, c.right) for c in first] == [
            (c.expiry, c.strike, c.right) for c in second
        ]
        assert len(first) == 3
