"""Tests for SaxoDiscovery and parse_saxo_option — all mocked, no network."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from algotrading.infra.universe import instrument_key, parse_instrument_key
from algotrading.infra.universe.contracts import Right
from algotrading.infra_saxo.collectors.saxo_discovery import (
    DiscoveryError,
    SaxoDiscovery,
    parse_saxo_option,
)

# ---------------------------------------------------------------------------
# parse_saxo_option — pure unit tests
# ---------------------------------------------------------------------------


def test_parse_saxo_option_call(sample_option_space_response) -> None:
    specific = sample_option_space_response["OptionSpace"][0]["SpecificOptions"][0]
    contract = parse_saxo_option(specific, symbol="SPY", expiry=date(2025, 6, 27), currency="USD")
    assert contract.right == Right.CALL
    assert contract.strike == Decimal("530.0")
    assert contract.broker_contract_id == "11111111"
    assert contract.symbol == "SPY"
    assert contract.currency == "USD"


def test_parse_saxo_option_put(sample_option_space_response) -> None:
    specific = sample_option_space_response["OptionSpace"][0]["SpecificOptions"][1]
    contract = parse_saxo_option(specific, symbol="SPY", expiry=date(2025, 6, 27), currency="USD")
    assert contract.right == Right.PUT
    assert contract.strike == Decimal("530.0")


def test_parse_saxo_option_missing_field_raises() -> None:
    with pytest.raises(DiscoveryError):
        parse_saxo_option({}, symbol="SPY", expiry=date(2025, 6, 27), currency="USD")


def test_parse_saxo_option_invalid_strike_raises() -> None:
    with pytest.raises(DiscoveryError):
        parse_saxo_option(
            {"PutCall": "Call", "StrikePrice": "bad", "Uic": 1},
            symbol="SPY",
            expiry=date(2025, 6, 27),
            currency="USD",
        )


def test_instrument_key_round_trip(sample_option_space_response) -> None:
    """instrument_key(parse_saxo_option(...)) must round-trip via parse_instrument_key."""
    specific = sample_option_space_response["OptionSpace"][0]["SpecificOptions"][0]
    contract = parse_saxo_option(specific, symbol="SPY", expiry=date(2025, 6, 27), currency="USD")
    key = instrument_key(contract)
    recovered = parse_instrument_key(key)
    assert recovered.symbol == contract.symbol
    assert recovered.strike == contract.strike
    assert recovered.right == contract.right
    assert recovered.expiry == contract.expiry


def test_parse_saxo_option_golden_instrument_key(sample_option_space_response) -> None:
    """Pin the exact Saxo SpecificOptions -> canonical instrument_key (leaf-to-canonical contract)."""
    specific = sample_option_space_response["OptionSpace"][0]["SpecificOptions"][0]
    contract = parse_saxo_option(
        specific, symbol="ASML", expiry=date(2026, 9, 18), currency="EUR", exchange="AMS"
    )
    assert instrument_key(contract) == "OPT:ASML:OPT:20260918:C:530:100:AMS:EUR"


# ---------------------------------------------------------------------------
# SaxoDiscovery — mocked transport
# ---------------------------------------------------------------------------


def _make_discovery(instruments_resp, option_space_resp):
    transport = MagicMock()
    transport.get.side_effect = [instruments_resp, option_space_resp]
    return SaxoDiscovery(transport)


def test_resolve_underlying_finds_spy(sample_instruments_response) -> None:
    transport = MagicMock()
    transport.get.return_value = sample_instruments_response
    discovery = SaxoDiscovery(transport)
    underlying = discovery.resolve_underlying("SPY")
    assert underlying.symbol == "SPY"
    assert underlying.uic == 9999
    assert underlying.option_root_id == 12345
    assert underlying.currency == "USD"


def test_resolve_underlying_not_found_raises() -> None:
    transport = MagicMock()
    transport.get.return_value = {"Data": []}
    discovery = SaxoDiscovery(transport)
    with pytest.raises(DiscoveryError, match="not found"):
        discovery.resolve_underlying("UNKNOWN")


def test_fetch_option_space_parses_all_contracts(
    sample_instruments_response, sample_option_space_response
) -> None:
    discovery = _make_discovery(sample_instruments_response, sample_option_space_response)
    contracts = discovery.fetch("SPY")
    # 2 strikes × 2 sides = 4 contracts
    assert len(contracts) == 4
    rights = {c.right for c in contracts}
    assert rights == {Right.CALL, Right.PUT}
    strikes = {c.strike for c in contracts}
    assert Decimal("530") in strikes
    assert Decimal("535") in strikes


def test_fetch_overrides_broker_contract_id_with_underlying_uic(
    sample_instruments_response, sample_option_space_response
) -> None:
    """The adapter subscribes the chain by the UNDERLYING Uic (9999 in the fixture), so every
    contract's broker_contract_id must carry it; the strike's own Uic moves into raw."""
    discovery = _make_discovery(sample_instruments_response, sample_option_space_response)
    contracts = discovery.fetch("SPY")
    assert all(c.broker_contract_id == "9999" for c in contracts)
    by_id = {c.raw["strike_uic"] for c in contracts}
    assert by_id == {11111111, 11111112, 11111113, 11111114}  # per-strike Uics from the fixture
    # The real exchange (OPRA in the fixture) is preserved in raw; the parser's own raw
    # fields (saxo_uic, put_call) survive the merge.
    assert all(c.raw["exchange"] == "OPRA" for c in contracts)
    assert all("saxo_uic" in c.raw and "put_call" in c.raw for c in contracts)


def test_fetch_builds_new_contracts_without_mutating_frozen_dataclass(
    sample_instruments_response, sample_option_space_response
) -> None:
    """Identity fields are untouched (canonical key byte-identical: exchange SAXO_<uic>),
    and the contracts pass frozen-dataclass validation — no object.__setattr__ backdoor."""
    discovery = _make_discovery(sample_instruments_response, sample_option_space_response)
    contracts = discovery.fetch("SPY")
    call_530 = next(c for c in contracts if c.right == Right.CALL and c.strike == Decimal("530.0"))
    # Derived by hand from the canonical key format TYPE:SYMBOL:SEC:EXPIRY:RIGHT:STRIKE:MULT:EXCH:CCY
    assert instrument_key(call_530) == "OPT:SPY:OPT:20250627:C:530:100:SAXO_9999:USD"
    # The instance is genuinely frozen — assignment must raise, proving no thawed copy leaks out.
    with pytest.raises(AttributeError):
        call_530.broker_contract_id = "tampered"  # type: ignore[misc]


def test_fetch_option_space_bad_expiry_raises(sample_instruments_response) -> None:
    bad_resp = {"OptionSpace": [{"DisplayExpiry": "not-a-date", "SpecificOptions": []}]}
    transport = MagicMock()
    transport.get.side_effect = [sample_instruments_response, bad_resp]
    discovery = SaxoDiscovery(transport)
    with pytest.raises(DiscoveryError, match="Cannot parse expiry"):
        discovery.fetch("SPY")
