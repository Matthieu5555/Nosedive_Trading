"""Runtime index conid + option-month resolution over CP REST (WS 1C step 3).

The live capture path resolves each index's conid from its symbol at fire time, so the
registry's ``conid: 0`` placeholder is never trusted. These tests pin the selection against a
fake ``/secdef/search`` response:

* the IND section on the requested routing exchange (CBOE/EUREX) selects the index conid —
  not a same-symbol stock, not the same symbol's index on a different venue;
* the OPT section's ``months`` string parses to the listed month tokens;
* an unresolvable response raises a labeled error rather than guessing a conid.

No network: a fake transport returns a canned search response.
"""

from __future__ import annotations

from typing import Any

import pytest
from algotrading.infra_ibkr.collectors.cp_rest_index import (
    IndexConidError,
    parse_index_conid,
    parse_option_months,
    resolve_index,
)


class _FakeSearch:
    def __init__(self, results: Any) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert path == "/iserver/secdef/search"
        self.calls.append(dict(params or {}))
        return self.results


# A realistic SPX search response: the index routes IND on CBOE and lists option months; a
# same-symbol STK row and an IND on a different venue are present as distractors.
_SPX_SEARCH = [
    {
        "conid": 416904,
        "symbol": "SPX",
        "sections": [
            {"secType": "IND", "exchange": "CBOE;NASDAQ"},
            {"secType": "OPT", "months": "JUN26;JUL26;SEP26", "exchange": "CBOE"},
        ],
    },
    {"conid": 999999, "symbol": "SPX", "sections": [{"secType": "STK", "exchange": "NYSE"}]},
]


def test_index_conid_selected_by_ind_section_on_the_routing_exchange() -> None:
    # The CBOE-routed IND row wins over the same-symbol STK row.
    assert parse_index_conid(_SPX_SEARCH, symbol="SPX", exchange="CBOE") == 416904


def test_wrong_exchange_does_not_resolve() -> None:
    # No IND section routes through EUREX for SPX -> labeled error, never the STK conid.
    with pytest.raises(IndexConidError):
        parse_index_conid(_SPX_SEARCH, symbol="SPX", exchange="EUREX")


def test_option_months_parse_in_listed_order_deduplicated() -> None:
    assert parse_option_months(_SPX_SEARCH, symbol="SPX") == ("JUN26", "JUL26", "SEP26")


def test_no_option_section_yields_empty_months_not_a_crash() -> None:
    results = [{"conid": 1, "symbol": "SPX", "sections": [{"secType": "IND", "exchange": "CBOE"}]}]
    assert parse_option_months(results, symbol="SPX") == ()


def test_resolve_index_returns_conid_and_months_from_one_search() -> None:
    transport = _FakeSearch(_SPX_SEARCH)
    resolved = resolve_index(transport, symbol="SPX", exchange="CBOE")
    assert resolved.conid == 416904
    assert resolved.option_months == ("JUN26", "JUL26", "SEP26")
    # The search omitted the name field (the documented gotcha) and asked for the IND secType.
    assert "name" not in transport.calls[0]
    assert transport.calls[0]["secType"] == "IND"


def test_unresolvable_search_raises_labeled_error() -> None:
    with pytest.raises(IndexConidError):
        parse_index_conid([{"conid": 1, "symbol": "OTHER"}], symbol="SPX", exchange="CBOE")
