"""Client Portal option-chain discovery (ADR 0024) — search → strikes → info, against a fake.

No live Gateway: a fake transport routed by path returns canned responses. The IBKR-specific risks
are pinned: the ``name`` field is **omitted** on ``/secdef/search`` (sending it suppresses strikes),
and the wire shapes parse correctly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from algotrading.infra_ibkr.collectors.cp_rest_discovery import (
    CpRestDiscovery,
    DiscoveryError,
    parse_info_contract,
    parse_strikes,
)


class _FakeTransport:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, dict(params or {})))
        return self._responses[path]


def test_underlying_conid_omits_name_field() -> None:
    transport = _FakeTransport(
        {"/iserver/secdef/search": [{"conid": 265598, "symbol": "SPY", "secType": "STK"}]}
    )
    discovery = CpRestDiscovery(transport)
    assert discovery.underlying_conid("SPY") == 265598
    (path, params) = transport.calls[0]
    assert path == "/iserver/secdef/search"
    assert "name" not in params  # the documented gotcha: name suppresses /strikes
    assert params["symbol"] == "SPY"


def test_underlying_conid_unresolved_raises() -> None:
    transport = _FakeTransport({"/iserver/secdef/search": [{"conid": 1, "symbol": "OTHER"}]})
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
    transport = _FakeTransport(
        {
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
    (path, params) = transport.calls[0]
    assert path == "/iserver/secdef/info"
    assert params["conid"] == 265598 and params["right"] == "C"
