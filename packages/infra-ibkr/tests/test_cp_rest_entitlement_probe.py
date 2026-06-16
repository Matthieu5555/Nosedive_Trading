from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    IbkrRef,
    IndexEntry,
    MembershipChange,
    MembershipRankingError,
    ingest_membership_changes,
)
from algotrading.infra_ibkr.collectors.cp_rest_entitlement_probe import (
    PROBE_OUTCOMES,
    ProbeResult,
    format_probe_table,
    probe_index_entitlement,
)
from algotrading.infra_ibkr.connectivity.cp_rest_transport import CpRestTransportError

SX5E = IndexEntry(
    "SX5E",
    "EURO STOXX 50",
    "XEUR",
    "EUR",
    IbkrRef(0, "IND", "EUREX", symbol="ESTX50", constituent_conids=(("SAN1", 29612249),)),
    True,
)
TRADE_DATE = date(2026, 3, 12)
KNOWN = date(2026, 1, 1)
VENDOR = "Test"

_MONTHS = ("JUN26", "SEP26")
_STRIKES = (90.0, 95.0, 100.0, 105.0, 110.0)
_SPOT = 101.0

_EQUITY_CONID = {"ASML": 600001, "TTE": 600002, "SIE": 600003, "ADS": 600004, "ENEL": 600005}
_SAN1_CONID = 29612249

_QUOTE_POLICY = {
    "ASML": "two_sided",
    "TTE": "one_sided",
    "SIE": "no_quote",
    "ADS": "unentitled",
    "SAN1": "two_sided",
    "ENEL": "no_options",
    "GHOST": "unresolved",
}


def _option_conid(underlying_conid: int, strike: float, right: str) -> int:
    return underlying_conid * 1000 + int(strike) * 2 + (0 if right == "C" else 1)


class _FakeGateway:

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stk_searches: list[str] = []
        self._conid_to_name = {conid: name for name, conid in _EQUITY_CONID.items()}
        self._conid_to_name[_SAN1_CONID] = "SAN1"
        self._conid_for = {**_EQUITY_CONID, "SAN1": _SAN1_CONID}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "/iserver/secdef/search":
            return self._search(params)
        if path == "/iserver/secdef/strikes":
            return {"call": list(_STRIKES), "put": list(_STRIKES)}
        if path == "/iserver/secdef/info":
            return self._info(params)
        if path == "/iserver/marketdata/snapshot":
            return self._snapshot(params)
        raise AssertionError(f"unexpected path {path!r}")

    def _search(self, params: dict[str, Any]) -> Any:
        sec_type = str(params.get("secType", ""))
        symbol = str(params.get("symbol", ""))
        if sec_type == "IND":
            return [{"conid": 320227571, "symbol": "ESTX50",
                     "sections": [{"secType": "IND", "exchange": "EUREX"}]}]
        if sec_type == "STK":
            assert symbol != "SAN1", "pinned SAN1 conid must come from the pin, never a search"
            self.stk_searches.append(symbol)
            conid = self._conid_for.get(symbol)
            if conid is None:
                return []
            return [{"conid": conid, "symbol": symbol, "sections": [{"secType": "STK"}]}]
        conid = self._conid_for.get(symbol)
        if conid is None:
            return []
        opt: list[dict[str, Any]] = []
        if _QUOTE_POLICY.get(symbol) != "no_options":
            opt = [{"secType": "OPT", "months": ";".join(_MONTHS), "exchange": "EUREX"}]
        return [{"conid": conid, "symbol": symbol,
                 "sections": [{"secType": "STK", "exchange": "EUREX"}, *opt]}]

    def _info(self, params: dict[str, Any]) -> Any:
        underlying_conid = int(params["conid"])
        strike = float(params["strike"])
        right = str(params["right"])
        return [{"conid": str(_option_conid(underlying_conid, strike, right)),
                 "maturityDate": "20260619", "strike": str(strike), "right": right}]

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows: list[dict[str, Any]] = []
        for conid_text in str(params["conids"]).split(","):
            conid = int(conid_text)
            if conid in self._conid_to_name:
                rows.append({"conid": conid, "31": str(_SPOT), "_updated": 0})
                continue
            name = self._owner_of(conid)
            policy = _QUOTE_POLICY.get(name, "two_sided")
            if policy == "unentitled":
                raise CpRestTransportError("forbidden", status_code=403)
            row: dict[str, Any] = {"conid": conid, "_updated": 0}
            if policy == "two_sided":
                row["84"] = "3.10"
                row["86"] = "3.30"
            elif policy == "one_sided":
                row["84"] = "3.10"
            rows.append(row)
        return rows

    def _owner_of(self, conid: int) -> str:
        for name, underlying_conid in self._conid_for.items():
            if conid == underlying_conid:
                return name
            for strike in _STRIKES:
                for right in ("C", "P"):
                    if _option_conid(underlying_conid, strike, right) == conid:
                        return name
        raise AssertionError(f"no owner for snapshot conid {conid}")


@pytest.fixture
def store(tmp_path: Any) -> ParquetStore:
    store = ParquetStore(tmp_path)
    weights = {
        "ASML": 0.30, "TTE": 0.20, "SIE": 0.18, "ADS": 0.12,
        "SAN1": 0.10, "ENEL": 0.06, "GHOST": 0.04,
    }
    ingest_membership_changes(
        store,
        [MembershipChange("SX5E", name, KNOWN, None, KNOWN, VENDOR, w) for name, w in weights.items()],
        complete_snapshot=True,
    )
    return store


def _probe(store: ParquetStore, top_n: int, gateway: _FakeGateway | None = None) -> ProbeResult:
    return probe_index_entitlement(
        gateway or _FakeGateway(), store=store, index=SX5E, as_of_date=TRADE_DATE, top_n=top_n
    )


def test_every_verdict_is_classified_from_the_gateway_policy(store: ParquetStore) -> None:
    result = _probe(store, top_n=7)
    by_name = {row.constituent: row.outcome for row in result.per_name}
    assert by_name == {
        "ASML": "two_sided",
        "TTE": "one_sided",
        "SIE": "no_quote",
        "ADS": "unentitled",
        "SAN1": "two_sided",
        "ENEL": "no_options",
        "GHOST": "unresolved",
    }


def test_counts_tally_every_outcome_zero_filled(store: ParquetStore) -> None:
    result = _probe(store, top_n=7)
    assert set(result.counts) == set(PROBE_OUTCOMES)
    assert sum(result.counts.values()) == 7
    assert result.counts == {
        "unresolved": 1, "no_options": 1, "unentitled": 1,
        "no_quote": 1, "one_sided": 1, "two_sided": 2,
    }


def test_entitled_is_exactly_the_two_sided_names(store: ParquetStore) -> None:
    result = _probe(store, top_n=7)
    assert set(result.entitled) == {"ASML", "SAN1"}


def test_near_atm_strike_is_the_one_nearest_spot(store: ParquetStore) -> None:
    result = _probe(store, top_n=1)
    asml = result.per_name[0]
    assert asml.constituent == "ASML"
    assert asml.strike == 100.0
    assert asml.expiry == "JUN26"


def test_pinned_constituent_is_reached_by_conid_never_searched(store: ParquetStore) -> None:
    gateway = _FakeGateway()
    result = _probe(store, top_n=5, gateway=gateway)
    san1 = next(row for row in result.per_name if row.constituent == "SAN1")
    assert san1.outcome == "two_sided"
    assert san1.conid == _SAN1_CONID
    assert "SAN1" not in gateway.stk_searches


def test_top_n_selects_the_heaviest_in_rank_order(store: ParquetStore) -> None:
    result = _probe(store, top_n=3)
    assert [row.constituent for row in result.per_name] == ["ASML", "TTE", "SIE"]
    assert [row.rank for row in result.per_name] == [1, 2, 3]
    assert [row.weight for row in result.per_name] == [0.30, 0.20, 0.18]


def test_request_budget_is_a_small_constant_per_name(store: ParquetStore) -> None:
    gateway = _FakeGateway()
    _probe(store, top_n=1, gateway=gateway)
    assert len(gateway.calls) == 7
    paths = [path for path, _ in gateway.calls]
    assert paths.count("/iserver/secdef/strikes") == 1
    assert paths.count("/iserver/secdef/info") == 2


def test_pinned_name_skips_the_conid_search_one_fewer_call(store: ParquetStore) -> None:
    gateway = _FakeGateway()
    _probe(store, top_n=5, gateway=gateway)
    assert set(gateway.stk_searches) == {"ASML", "TTE", "SIE", "ADS"}
    assert "SAN1" not in gateway.stk_searches


def test_no_banked_membership_is_an_empty_result_not_a_raise(tmp_path: Any) -> None:
    empty = ParquetStore(tmp_path)
    result = probe_index_entitlement(
        _FakeGateway(), store=empty, index=SX5E, as_of_date=TRADE_DATE, top_n=3
    )
    assert result.per_name == ()
    assert sum(result.counts.values()) == 0
    assert result.entitled == ()


def test_a_missing_weight_basket_is_a_loud_ranking_failure(tmp_path: Any) -> None:
    partial = ParquetStore(tmp_path)
    ingest_membership_changes(
        partial,
        [
            MembershipChange("SX5E", "ASML", KNOWN, None, KNOWN, VENDOR, 0.40),
            MembershipChange("SX5E", "TTE", KNOWN, None, KNOWN, VENDOR, None),
        ],
    )
    with pytest.raises(MembershipRankingError, match="cannot rank a basket"):
        probe_index_entitlement(
            _FakeGateway(), store=partial, index=SX5E, as_of_date=TRADE_DATE, top_n=2
        )


def test_the_probe_writes_nothing_to_the_store(store: ParquetStore) -> None:
    before = {p.name for p in store.root.rglob("*") if p.is_dir()}
    _probe(store, top_n=7)
    after = {p.name for p in store.root.rglob("*") if p.is_dir()}
    assert before == after
    assert any(p.name == "index_constituents" for p in store.root.rglob("index_constituents"))
    assert not list(store.root.rglob("*entitlement*"))
    assert not list(store.root.rglob("*probe*"))


def test_table_renders_every_name_and_a_summary(store: ParquetStore) -> None:
    table = format_probe_table(_probe(store, top_n=7))
    for name in ("ASML", "TTE", "SIE", "ADS", "SAN1", "ENEL", "GHOST"):
        assert name in table
    assert "two_sided=2" in table
    assert "unentitled=1" in table
    assert "entitled (two-sided): ASML, SAN1" in table
