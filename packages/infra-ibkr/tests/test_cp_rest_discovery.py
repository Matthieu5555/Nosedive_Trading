"""Client Portal option-chain discovery (ADR 0024) — search → strikes → info, against a fake.

No live Gateway: a fake transport routed by path returns canned responses. The IBKR-specific risks
are pinned: the ``name`` field is **omitted** on ``/secdef/search`` (sending it suppresses strikes),
and the wire shapes parse correctly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from algotrading.infra_ibkr.collectors.cp_rest_discovery import (
    CpRestDiscovery,
    DiscoveryError,
    parse_info_contract,
    parse_strikes,
)

from .conftest import FakeCpTransport


def test_underlying_conid_omits_name_field() -> None:
    transport = FakeCpTransport(
        get_routes={"/iserver/secdef/search": [{"conid": 265598, "symbol": "SPY", "secType": "STK"}]}
    )
    discovery = CpRestDiscovery(transport)
    assert discovery.underlying_conid("SPY") == 265598
    (path, params) = transport.get_calls[0]
    assert path == "/iserver/secdef/search"
    assert "name" not in params  # the documented gotcha: name suppresses /strikes
    assert params["symbol"] == "SPY"


def test_underlying_conid_unresolved_raises() -> None:
    transport = FakeCpTransport(
        get_routes={"/iserver/secdef/search": [{"conid": 1, "symbol": "OTHER"}]}
    )
    with pytest.raises(DiscoveryError):
        CpRestDiscovery(transport).underlying_conid("SPY")


def test_parse_strikes_sorts_calls_and_puts() -> None:
    calls, puts = parse_strikes({"call": [760, 755, 758], "put": [758, 755]})
    assert calls == (755.0, 758.0, 760.0)
    assert puts == (755.0, 758.0)


def test_parse_info_contract_builds_option_contract() -> None:
    contract = parse_info_contract(
        {"conid": "987654", "maturityDate": "20260626", "strike": "758", "right": "C"},
        symbol="SPY",
        exchange="SMART",
        currency="USD",
    )
    assert contract.symbol == "SPY"
    assert contract.expiry == date(2026, 6, 26)
    assert contract.strike == Decimal("758")
    assert str(contract.right) == "C"
    assert contract.broker_contract_id == "987654"


def test_parse_info_contract_malformed_raises() -> None:
    with pytest.raises(DiscoveryError):
        parse_info_contract({"conid": "1"}, symbol="SPY", exchange="SMART", currency="USD")


def test_contracts_walks_info_for_one_strike() -> None:
    transport = FakeCpTransport(
        get_routes={
            "/iserver/secdef/info": [
                {"conid": "987654", "maturityDate": "20260626", "strike": "758", "right": "C"}
            ]
        }
    )
    contracts = CpRestDiscovery(transport).contracts(
        265598, symbol="SPY", month="JUN26", strike=758, right="C"
    )
    assert len(contracts) == 1
    assert contracts[0].broker_contract_id == "987654"
    # The info call carried the conid + (month, strike, right) — never a name field.
    (path, params) = transport.get_calls[0]
    assert path == "/iserver/secdef/info"
    assert params["conid"] == 265598 and params["right"] == "C"


def test_underlying_conid_prefers_the_stock_row_over_a_futures_root() -> None:
    # The live gotcha behind the 43 failed backfill names: /secdef/search?symbol=BA returns
    # "BARLEY FUTURES ASX" FIRST and Boeing NYSE second — top-level secType is null on the
    # wire; the truth is in sections[].secType. The first symbol-matching row carrying an
    # STK section must win, never blind first-match.
    transport = FakeCpTransport(
        get_routes={
            "/iserver/secdef/search": [
                {
                    "conid": "11673684",
                    "symbol": "BA",
                    "secType": None,
                    "companyName": "BARLEY FUTURES",
                    "sections": [{"secType": "IND"}, {"secType": "FUT"}, {"secType": "BAG"}],
                },
                {
                    "conid": "4762",
                    "symbol": "BA",
                    "secType": None,
                    "companyName": "BOEING CO/THE",
                    "sections": [{"secType": "STK"}, {"secType": "OPT"}],
                },
            ]
        }
    )
    assert CpRestDiscovery(transport).underlying_conid("BA") == 4762


def test_underlying_conid_falls_back_to_first_match_when_no_stock_row() -> None:
    # A response with no STK section anywhere (older fixtures carry a top-level secType and
    # no sections) keeps the previous first-match behavior rather than failing.
    transport = FakeCpTransport(
        get_routes={"/iserver/secdef/search": [{"conid": 265598, "symbol": "SPY", "secType": "STK"}]}
    )
    assert CpRestDiscovery(transport).underlying_conid("SPY") == 265598


def test_underlying_conid_prefers_the_currency_consistent_venue() -> None:
    # Live: 'SAF' lists SARATOGA (VALUE, a dead aggregated listing) before SAFRAN (SBF).
    # An EUR-currency discovery (an SX5E constituent sweep) must pick the EUR-venue row.
    transport = FakeCpTransport(
        get_routes={
            "/iserver/secdef/search": [
                {"conid": "331451987", "symbol": "SAF", "companyName": "SARATOGA INVESTMENT",
                 "description": "VALUE", "sections": [{"secType": "STK"}]},
                {"conid": "1322028", "symbol": "SAF", "companyName": "SAFRAN SA",
                 "description": "SBF", "sections": [{"secType": "STK"}]},
            ]
        }
    )
    assert CpRestDiscovery(transport, currency="EUR").underlying_conid("SAF") == 1322028


def test_underlying_conid_currency_venue_beats_a_foreign_homonym() -> None:
    # Live: 'ITX' lists ITX GROUP (VALUE) then ITACONIX (LSE) then INDITEX (BM). A naive
    # "first non-VALUE stock" picks the wrong LSE company; the EUR-venue rule picks Inditex.
    transport = FakeCpTransport(
        get_routes={
            "/iserver/secdef/search": [
                {"conid": "44200850", "symbol": "ITX", "companyName": "ITX GROUP LTD",
                 "description": "VALUE", "sections": [{"secType": "STK"}]},
                {"conid": "649910368", "symbol": "ITX", "companyName": "ITACONIX PLC",
                 "description": "LSE", "sections": [{"secType": "STK"}]},
                {"conid": "162084958", "symbol": "ITX", "companyName": "INDITEX",
                 "description": "BM", "sections": [{"secType": "STK"}]},
            ]
        }
    )
    assert CpRestDiscovery(transport, currency="EUR").underlying_conid("ITX") == 162084958


def test_underlying_conid_avoids_a_dead_value_listing_without_currency_match() -> None:
    # No venue matches the discovery currency: the dead VALUE listing still loses to a
    # real venue (the USD sweep shape — primary listings are never on VALUE).
    transport = FakeCpTransport(
        get_routes={
            "/iserver/secdef/search": [
                {"conid": "1", "symbol": "XYZ", "companyName": "DEAD LISTING",
                 "description": "VALUE", "sections": [{"secType": "STK"}]},
                {"conid": "2", "symbol": "XYZ", "companyName": "REAL CO",
                 "description": "ASX", "sections": [{"secType": "STK"}]},
            ]
        }
    )
    assert CpRestDiscovery(transport, currency="USD").underlying_conid("XYZ") == 2
