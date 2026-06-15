"""The single-name Eurex option entitlement probe (T-§7.4 pre-flight).

The seam this pins: given an authenticated CP REST transport (a fake gateway here — NO network, NO
secrets) and a fired index whose banked membership has known weights, the probe resolves the
point-in-time top-N constituents and, per name, spends a *handful* of read-only calls to classify
whether the account returns a tradeable two-sided Eurex option quote — without paying the full
discovery+capture cost.

Every expectation is derived independently of the probe code. The fake gateway is built per name
from an explicit *policy* (the quote shape it returns, or a refusal/empty), and each test hand-
derives the verdict the probe MUST reach from that policy — never reading back what the code
emitted. Coverage: two-sided, one-sided, no-quote, unentitled (403), no_options, unresolved, the
pinned-conid path, the top-N ranking, and the request-frugality budget (the whole reason the probe
exists): the probe must make only O(small constant) calls per name, not a full chain walk.
"""

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

# ---------------------------------------------------------------------------------------------
# The probed index (SX5E→ESTX50 on IBKR, with the pinned Sanofi disambiguation SAN1) and a small
# known chain, mirroring the constituent-capture fixtures so the two lanes share a vocabulary.
# ---------------------------------------------------------------------------------------------
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

_MONTHS = ("JUN26", "SEP26")  # the search lists them near→far; the probe must pick JUN26
_STRIKES = (90.0, 95.0, 100.0, 105.0, 110.0)
_SPOT = 101.0  # nearest listed strike is 100.0 — the probe's near-ATM pick (independent of code)

# The equity conids the STK secdef search resolves the unpinned names to. SAN1 is pinned. ENEL
# resolves a conid (so it is not 'unresolved') but its symbol search lists no OPT section
# ('no_options'); GHOST resolves no conid at all ('unresolved').
_EQUITY_CONID = {"ASML": 600001, "TTE": 600002, "SIE": 600003, "ADS": 600004, "ENEL": 600005}
_SAN1_CONID = 29612249

# Per-name quote policy: how the gateway answers the snapshot for that name's near-ATM contracts.
#   "two_sided"  -> both bid and ask populated
#   "one_sided"  -> only a bid
#   "no_quote"   -> neither bid nor ask (a dark, subscribed contract)
#   "unentitled" -> the gateway refuses the contract snapshot with HTTP 403
#   "no_options" -> the symbol search lists no OPT section
#   "unresolved" -> the STK search resolves no conid (and it is not pinned)
_QUOTE_POLICY = {
    "ASML": "two_sided",
    "TTE": "one_sided",
    "SIE": "no_quote",
    "ADS": "unentitled",
    "SAN1": "two_sided",  # the pinned name, reached by its conid (never a STK search)
    "ENEL": "no_options",
    "GHOST": "unresolved",
}


def _option_conid(underlying_conid: int, strike: float, right: str) -> int:
    """A deterministic, collision-free option conid keyed on its underlying — the gateway's id."""
    return underlying_conid * 1000 + int(strike) * 2 + (0 if right == "C" else 1)


class _FakeGateway:
    """A fake CP REST gateway whose per-name responses are driven by ``_QUOTE_POLICY``.

    Records every call so the request-frugality budget is observable. The index resolves via an
    ``IND`` search; an unpinned name's conid via a ``STK`` search (pinned SAN1 is never searched —
    asserted); option months via a symbol search filtered by the resolved conid. The near-ATM
    call+put snapshot returns the bid/ask shape the name's policy dictates, or a 403 for an
    unentitled name.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stk_searches: list[str] = []
        # conid -> name, to recognise a snapshot row as an underlying spot (vs an option mark).
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
            if conid is None:  # an unresolved name (GHOST) — the search lists nothing
                return []
            return [{"conid": conid, "symbol": symbol, "sections": [{"secType": "STK"}]}]
        # A symbol-keyed search with no secType is the option-month lookup. The OPT section is
        # present unless the name's policy is no_options.
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
            # An underlying spot snapshot (the chain-centring read) always returns the real spot,
            # independent of the name's option-quote policy — the policy governs only the option
            # marks below. A 403 there would mask the spot read; the entitlement refusal is on the
            # option contracts.
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
                row["84"] = "3.10"  # bid only
            # no_quote: neither side populated
            rows.append(row)
        return rows

    def _owner_of(self, conid: int) -> str:
        """Which constituent a snapshot conid belongs to (its underlying or one of its options)."""
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
    """A temp Parquet store with the SX5E membership banked (NEVER the canonical data/).

    Seven names with hand-set, descending weights, so the top-N is unambiguous: ASML(1) > TTE(2) >
    SIE(3) > ADS(4) > SAN1(5) > ENEL(6) > GHOST(7). Weights sum to 1.0 (a complete snapshot).
    """
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


# ---------------------------------------------------------------------------------------------
# Per-verdict classification — each derived independently from the gateway's policy.
# ---------------------------------------------------------------------------------------------
def test_every_verdict_is_classified_from_the_gateway_policy(store: ParquetStore) -> None:
    result = _probe(store, top_n=7)
    by_name = {row.constituent: row.outcome for row in result.per_name}
    # Independently derived from _QUOTE_POLICY (NOT read back from the code):
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
    # Every vocabulary key present (zero-filled), summing to the names probed.
    assert set(result.counts) == set(PROBE_OUTCOMES)
    assert sum(result.counts.values()) == 7
    assert result.counts == {
        "unresolved": 1, "no_options": 1, "unentitled": 1,
        "no_quote": 1, "one_sided": 1, "two_sided": 2,
    }


def test_entitled_is_exactly_the_two_sided_names(store: ParquetStore) -> None:
    result = _probe(store, top_n=7)
    # The whole point: the names worth a full-depth capture are precisely the two-sided ones.
    assert set(result.entitled) == {"ASML", "SAN1"}


def test_near_atm_strike_is_the_one_nearest_spot(store: ParquetStore) -> None:
    # Spot 101.0 → nearest listed strike is 100.0; the probed expiry is the NEAREST month (JUN26).
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
    assert san1.conid == _SAN1_CONID  # the pinned conid, not a searched one
    assert "SAN1" not in gateway.stk_searches  # the gateway also asserts this internally


def test_top_n_selects_the_heaviest_in_rank_order(store: ParquetStore) -> None:
    result = _probe(store, top_n=3)
    # top-3 by weight: ASML(.30)=1, TTE(.20)=2, SIE(.18)=3 — never ADS/SAN1/ENEL/GHOST.
    assert [row.constituent for row in result.per_name] == ["ASML", "TTE", "SIE"]
    assert [row.rank for row in result.per_name] == [1, 2, 3]
    assert [row.weight for row in result.per_name] == [0.30, 0.20, 0.18]


# ---------------------------------------------------------------------------------------------
# Request frugality — the feature: a handful of calls per name, NOT a full chain walk.
# ---------------------------------------------------------------------------------------------
def test_request_budget_is_a_small_constant_per_name(store: ParquetStore) -> None:
    gateway = _FakeGateway()
    _probe(store, top_n=1, gateway=gateway)  # ASML alone: a two-sided name, the full chain
    # An unpinned two-sided name walks exactly:
    #   1 STK search (conid) + 1 symbol search (months) + 1 spot snapshot + 1 strikes
    #   + 2 info (call & put) + 1 contract snapshot = 7 calls. NOT hundreds.
    assert len(gateway.calls) == 7
    # And specifically: exactly ONE strikes call (the nearest month only, never every month) and
    # exactly TWO info calls (one strike × call+put, never the full ladder × every month).
    paths = [path for path, _ in gateway.calls]
    assert paths.count("/iserver/secdef/strikes") == 1
    assert paths.count("/iserver/secdef/info") == 2


def test_pinned_name_skips_the_conid_search_one_fewer_call(store: ParquetStore) -> None:
    # SAN1 is pinned, so it never spends the STK conid search: 6 calls, not 7. Probe top-5 then
    # diff the total against a derived budget so the saving is observable, not asserted blind.
    gateway = _FakeGateway()
    _probe(store, top_n=5, gateway=gateway)
    # 4 unpinned names (ASML, TTE, SIE, ADS) each spend the STK conid search exactly once; SAN1
    # (pinned) does not — it is reached by its conid, saving one call. The gateway records every STK
    # search by symbol, so the saving is directly observable (pinned SAN1 absent from the record).
    assert set(gateway.stk_searches) == {"ASML", "TTE", "SIE", "ADS"}
    assert "SAN1" not in gateway.stk_searches


# ---------------------------------------------------------------------------------------------
# Membership preconditions — read-only, and the shared resolver's loud failures propagate.
# ---------------------------------------------------------------------------------------------
def test_no_banked_membership_is_an_empty_result_not_a_raise(tmp_path: Any) -> None:
    # The probe is a diagnostic, not the fire: an unknown/unbanked index has nothing to probe and
    # returns an empty result cleanly (the script turns that into a labelled non-zero exit).
    empty = ParquetStore(tmp_path)
    result = probe_index_entitlement(
        _FakeGateway(), store=empty, index=SX5E, as_of_date=TRADE_DATE, top_n=3
    )
    assert result.per_name == ()
    assert sum(result.counts.values()) == 0
    assert result.entitled == ()


def test_a_missing_weight_basket_is_a_loud_ranking_failure(tmp_path: Any) -> None:
    # Membership present but unrankable (a labeled-unavailable weight): the shared resolver raises
    # MembershipRankingError (loud) — the probe never quietly truncates the top-N.
    partial = ParquetStore(tmp_path)
    ingest_membership_changes(
        partial,
        [
            MembershipChange("SX5E", "ASML", KNOWN, None, KNOWN, VENDOR, 0.40),
            MembershipChange("SX5E", "TTE", KNOWN, None, KNOWN, VENDOR, None),  # unknown weight
        ],
    )
    with pytest.raises(MembershipRankingError, match="cannot rank a basket"):
        probe_index_entitlement(
            _FakeGateway(), store=partial, index=SX5E, as_of_date=TRADE_DATE, top_n=2
        )


def test_the_probe_writes_nothing_to_the_store(store: ParquetStore) -> None:
    # READ-ONLY invariant: the probe reads membership but persists no table. After a full probe the
    # store holds only what the fixture banked (index_constituents), never a probe output table.
    before = {p.name for p in store.root.rglob("*") if p.is_dir()}
    _probe(store, top_n=7)
    after = {p.name for p in store.root.rglob("*") if p.is_dir()}
    assert before == after
    # The membership the fixture banked is the ONLY thing on disk — no probe output table appears.
    assert any(p.name == "index_constituents" for p in store.root.rglob("index_constituents"))
    assert not list(store.root.rglob("*entitlement*"))
    assert not list(store.root.rglob("*probe*"))


# ---------------------------------------------------------------------------------------------
# The printed table — a pure formatter the script prints.
# ---------------------------------------------------------------------------------------------
def test_table_renders_every_name_and_a_summary(store: ParquetStore) -> None:
    table = format_probe_table(_probe(store, top_n=7))
    for name in ("ASML", "TTE", "SIE", "ADS", "SAN1", "ENEL", "GHOST"):
        assert name in table
    assert "two_sided=2" in table
    assert "unentitled=1" in table
    assert "entitled (two-sided): ASML, SAN1" in table
