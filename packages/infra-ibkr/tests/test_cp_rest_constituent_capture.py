"""Widen the EOD close capture to an index's top-N constituents' option chains (T-§7.4, S1).

The seam this pins: given an authenticated CP REST transport (a fake gateway here — NO network,
NO secrets) and a fired index whose banked membership has known weights,
``collect_index_and_constituents_basket`` captures the index AND its point-in-time top-N
constituents' option chains, on the same grid / close instant, merged into one
:class:`IndexBasket` keyed by ``underlying`` — exactly the shape ``run_analytics`` consumes.

Every expectation is derived independently of the capture code: the fake gateway lists a known
chain for the index and each constituent (a fixed strikes × rights set, each with a known conid),
the membership store carries hand-set weights, and the test hand-derives which underlyings the
capture *must* return (the top-N by weight) and asserts the merged basket matches — never reading
back what the code emitted.

The membership top-N selector is now the shared :func:`algotrading.infra.universe.top_n_by_weight`
resolver (the stand-in stub was swapped for it on merge). Its ranking + incomplete-weight rejection
live in the membership tests; here we pin the lane's *use* of it: activation (all N attempted),
the fail-loud-on-empty guard, and the per-name outcome ledger.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StrikeSelectionConfig,
    UniverseConfig,
)
from algotrading.infra.actor import IndexBasket
from algotrading.infra.orchestration.eod_runner import FiredIndex
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    ChainSelection,
    IbkrRef,
    IndexEntry,
    MembershipChange,
    MembershipRankingError,
    ingest_membership_changes,
)
from algotrading.infra_ibkr.collectors.cp_rest_constituent_capture import (
    ConstituentLaneError,
    collect_index_and_constituents_basket,
)
from algotrading.infra_ibkr.live_capture import live_basket_source
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG

# ---------------------------------------------------------------------------------------------
# The fired index, its close, and a small known chain (mirrors the index-lane fixtures).
# ---------------------------------------------------------------------------------------------
# SX5E with a pinned constituent (SAN1, the Sanofi-disambiguation pin the registry carries) and an
# IBKR search override (SX5E lists as ESTX50 on IBKR) — both real-world wrinkles this lane handles.
SX5E = IndexEntry(
    "SX5E",
    "EURO STOXX 50",
    "XEUR",
    "EUR",
    IbkrRef(0, "IND", "EUREX", symbol="ESTX50", constituent_conids=(("SAN1", 29612249),)),
    True,
)
CLOSE = datetime(2026, 3, 12, 16, 30, tzinfo=UTC)  # EUREX close, UTC
NEXT_OPEN = datetime(2026, 3, 13, 7, 0, tzinfo=UTC)
TRADE_DATE = date(2026, 3, 12)
KNOWN = date(2026, 1, 1)
VENDOR = "Test"

INDEX_CONID = 320227571  # ESTX50
_MONTHS = {"JUN26": date(2026, 6, 19)}
_STRIKES = (95.0, 100.0, 105.0)
_SPOT = 100.0

# Equity conids the STK secdef search resolves the unpinned constituents to. SAN1 is pinned, so it
# is fetched by its conid and never searched (the gateway asserts that below).
_EQUITY_CONID = {"ASML": 600001, "TTE": 600002, "SIE": 600003}
# The membership basket, with hand-set weights. Five names; SAN1 (pinned) deliberately the 4th by
# weight so a top-3 EXCLUDES it (proves the pin does not sneak in a non-top-N name) and a top-4
# INCLUDES it (proves the pin path captures). Weights sum to 1.0 (a complete snapshot).
_WEIGHTS = {"ASML": 0.40, "TTE": 0.25, "SIE": 0.20, "SAN1": 0.10, "ENEL": 0.05}
# ENEL is a member by weight but the gateway lists NO options for it — proves a name with no chain
# is a clean per-name skip, not an abort.
_NO_OPTION_NAMES = {"ENEL"}
_ENEL_CONID = 600004


def _config(top_n: int) -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(
            version="u-1",
            exchange="EUREX",
            tenor_grid=("1m", "3m"),
            strike_selection=StrikeSelectionConfig(version="ss-1", min_strikes_per_side=3),
            constituent_top_n=top_n,
        ),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(
            version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
        ),
    )


def _selection() -> ChainSelection:
    return ChainSelection(max_expiries=1, min_strikes_per_side=3, option_exchange="EUREX")


def _option_conid(underlying_conid: int, expiry: date, strike: float, right: str) -> int:
    """A deterministic, collision-free option conid keyed on its underlying — the gateway's id."""
    base = underlying_conid * 1_000_000 + int(expiry.strftime("%y%m%d")) * 100
    return base + int(strike) * 2 + (0 if right == "C" else 1)


def _close_mark(strike: float, right: str) -> float:
    intrinsic = max(_SPOT - strike, 0.0) if right == "C" else max(strike - _SPOT, 0.0)
    return intrinsic + 3.0


class _FakeGateway:
    """A fake CP REST gateway listing a chain for the index and each option-bearing constituent.

    Routes by path + params. The index resolves via an ``IND`` secdef search; each unpinned
    constituent via a ``STK`` secdef search; pinned SAN1 is NEVER searched (asserted). Every
    underlying lists the same strikes × rights at a distinct, underlying-scoped conid, so the
    capture's per-underlying isolation is observable in the basket.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stk_searches: list[str] = []
        self._close_ms = int(CLOSE.timestamp() * 1000)
        self._conid_to_underlying = {INDEX_CONID: ("ESTX50", INDEX_CONID)}
        for name, conid in {**_EQUITY_CONID, "ENEL": _ENEL_CONID, "SAN1": 29612249}.items():
            self._conid_to_underlying[conid] = (name, conid)

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
        # A conid-keyed search (the option-month lookup for an already-resolved underlying) returns
        # the one row for that conid; a pinned name reaches the chain ONLY through this path.
        if "conid" in params and "secType" not in params:
            return self._search_by_conid(int(params["conid"]))
        sec_type = str(params.get("secType", ""))
        symbol = str(params.get("symbol", ""))
        if sec_type == "IND":
            return [
                {
                    "conid": INDEX_CONID,
                    "symbol": "ESTX50",
                    "sections": [
                        {"secType": "IND", "exchange": "EUREX"},
                        {"secType": "OPT", "months": ";".join(_MONTHS), "exchange": "EUREX"},
                    ],
                }
            ]
        # STK search resolves an UNPINNED constituent's conid. SAN1 is pinned: its conid never comes
        # from a ticker search, so a STK search for it is a bug in the capture path.
        assert symbol != "SAN1", "pinned SAN1 conid must come from the pin, never a ticker search"
        self.stk_searches.append(symbol)
        conid = {**_EQUITY_CONID, "ENEL": _ENEL_CONID}.get(symbol)
        if conid is None:
            return []
        return [{"conid": conid, "symbol": symbol, "sections": [{"secType": "STK"}]}]

    def _search_by_conid(self, conid: int) -> Any:
        name = {v: k for k, v in {**_EQUITY_CONID, "ENEL": _ENEL_CONID, "SAN1": 29612249}.items()}
        symbol = name.get(conid)
        if symbol is None:
            return []
        opt_section: list[dict[str, Any]] = []
        if symbol not in _NO_OPTION_NAMES:
            opt_section = [{"secType": "OPT", "months": ";".join(_MONTHS), "exchange": "EUREX"}]
        return [
            {
                "conid": conid,
                "symbol": symbol,
                "sections": [{"secType": "STK", "exchange": "EUREX"}, *opt_section],
            }
        ]

    def _info(self, params: dict[str, Any]) -> Any:
        underlying_conid = int(params["conid"])
        month = params["month"]
        strike = float(params["strike"])
        right = str(params["right"])
        expiry = _MONTHS[month]
        return [
            {
                "conid": str(_option_conid(underlying_conid, expiry, strike, right)),
                "maturityDate": expiry.strftime("%Y%m%d"),
                "strike": str(strike),
                "right": right,
            }
        ]

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows: list[dict[str, Any]] = []
        for conid_text in str(params["conids"]).split(","):
            conid = int(conid_text)
            if conid in self._conid_to_underlying:  # an underlying spot row
                rows.append({"conid": conid, "31": str(_SPOT), "_updated": self._close_ms})
                continue
            mark = self._mark_for_option_conid(conid)
            rows.append(
                {
                    "conid": conid,
                    "31": f"{mark:.2f}",
                    "84": f"{mark - 0.1:.2f}",
                    "86": f"{mark + 0.1:.2f}",
                    "_updated": self._close_ms,
                }
            )
        return rows

    def _mark_for_option_conid(self, conid: int) -> float:
        for _name, underlying_conid in self._conid_to_underlying.values():
            for expiry in _MONTHS.values():
                for strike in _STRIKES:
                    for right in ("C", "P"):
                        if _option_conid(underlying_conid, expiry, strike, right) == conid:
                            return _close_mark(strike, right)
        raise AssertionError(f"no mark for option conid {conid}")


@pytest.fixture
def store(tmp_path: Any) -> ParquetStore:
    """A temp Parquet store with the SX5E membership banked (NEVER the canonical data/)."""
    store = ParquetStore(tmp_path)
    ingest_membership_changes(
        store,
        [
            MembershipChange("SX5E", name, KNOWN, None, KNOWN, VENDOR, weight)
            for name, weight in _WEIGHTS.items()
        ],
        complete_snapshot=True,
    )
    return store


def _capture(store: ParquetStore, top_n: int) -> IndexBasket | None:
    return collect_index_and_constituents_basket(
        _FakeGateway(),
        store=store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(top_n),
        selection=_selection(),
    )


# ---------------------------------------------------------------------------------------------
# The widened capture.
# ---------------------------------------------------------------------------------------------
def test_capture_widens_to_the_top_n_constituents_by_weight(store: ParquetStore) -> None:
    basket = _capture(store, top_n=3)
    assert basket is not None
    # The index plus the top-3 constituents are the underlyings — never the 4th/5th by weight.
    underlyings = {k.underlying_symbol for k in basket.instruments}
    assert underlyings == {"SX5E", "ASML", "TTE", "SIE"}
    # Each underlying carries its OWN option chain (the full listed ladder here), keyed on it.
    for name in ("ASML", "TTE", "SIE"):
        opt = {
            (k.expiry, k.strike, k.option_right)
            for k in basket.instruments
            if k.is_option() and k.underlying_symbol == name
        }
        expected = {
            (expiry, strike, right)
            for expiry in _MONTHS.values()
            for strike in _STRIKES
            for right in ("C", "P")
        }
        assert opt == expected


def test_index_underlying_uses_the_resolved_conid_not_the_registry_placeholder(
    store: ParquetStore,
) -> None:
    basket = _capture(store, top_n=3)
    assert basket is not None
    index_leg = next(
        k for k in basket.instruments if k.underlying_symbol == "SX5E" and not k.is_option()
    )
    # Resolved at fire time from the ESTX50 search — never the registry's conid: 0 placeholder.
    assert index_leg.broker_contract_id == str(INDEX_CONID)


def test_pinned_constituent_is_fetched_by_conid_and_only_inside_its_top_n(
    store: ParquetStore,
) -> None:
    # top-3 EXCLUDES SAN1 (it is the 4th by weight) — the pin must not sneak a non-top-N chain in.
    top3 = _capture(store, top_n=3)
    assert top3 is not None
    assert "SAN1" not in {k.underlying_symbol for k in top3.instruments}
    # top-4 INCLUDES SAN1 — captured via its pinned conid (the gateway asserts it was never searched).
    top4 = _capture(store, top_n=4)
    assert top4 is not None
    san1_legs = [k for k in top4.instruments if k.underlying_symbol == "SAN1"]
    assert san1_legs  # SAN1 was captured
    san1_underlying = next(k for k in san1_legs if not k.is_option())
    assert san1_underlying.broker_contract_id == "29612249"  # the pinned conid


def test_a_constituent_with_no_listed_options_is_a_clean_per_name_skip(
    store: ParquetStore,
) -> None:
    # top-5 reaches ENEL (the 5th by weight), which the gateway lists NO options for. The fire must
    # NOT abort: the basket holds every option-bearing name and simply omits ENEL.
    basket = _capture(store, top_n=5)
    assert basket is not None
    underlyings = {k.underlying_symbol for k in basket.instruments}
    assert "ENEL" not in underlyings  # no chain → no legs, but no abort
    assert underlyings == {"SX5E", "ASML", "TTE", "SIE", "SAN1"}


def test_constituent_events_carry_the_same_session_close_as_the_index(
    store: ParquetStore,
) -> None:
    basket = _capture(store, top_n=3)
    assert basket is not None
    # Every event — index AND constituent — is stamped at the index's own session close.
    assert {e.canonical_ts for e in basket.events} == {CLOSE}
    assert {e.trade_date for e in basket.events} == {TRADE_DATE}
    # A constituent option's 'last' equals its independent close-mark oracle (per-underlying conid).
    by_key_field = {(e.instrument_key, e.field_name): e.value for e in basket.events}
    asml_105c = next(
        k for k in basket.instruments
        if k.is_option() and k.underlying_symbol == "ASML" and k.strike == 105.0
        and k.option_right == "C"
    )
    assert by_key_field[(asml_105c.canonical(), "last")] == _close_mark(105.0, "C")


def test_masters_accompany_every_instrument_as_of_the_close(store: ParquetStore) -> None:
    basket = _capture(store, top_n=3)
    assert basket is not None
    assert len(basket.masters) == len(basket.instruments)
    assert {m.as_of_date for m in basket.masters} == {TRADE_DATE}


def test_live_basket_source_with_a_store_routes_to_the_widened_capture(
    store: ParquetStore,
) -> None:
    """The production seam: a store-wired ``live_basket_source`` captures index + constituents.

    With ``store`` given (the production shim passes the runner's store) the bound source must use
    the widened capture; without it (the prior behaviour) it captures the index only. ``transport``
    is injected (the fake gateway) so the credential/socket path is bypassed; ``now`` is pinned to
    the trade date so the no-look-ahead guard admits this same-day fire.
    """
    fired = FiredIndex(entry=SX5E, as_of=CLOSE, next_open=NEXT_OPEN)

    widened = live_basket_source(
        transport=_FakeGateway(),
        config=_config(3),
        selection=_selection(),
        now=lambda: TRADE_DATE,
        store=store,
    )
    assert widened is not None
    basket = widened(fired, TRADE_DATE)
    assert basket is not None
    assert {k.underlying_symbol for k in basket.instruments} == {"SX5E", "ASML", "TTE", "SIE"}

    # No store → the index-only lane (the prior behaviour), proving the store is what widens scope.
    index_only = live_basket_source(
        transport=_FakeGateway(),
        config=_config(3),
        selection=_selection(),
        now=lambda: TRADE_DATE,
    )
    assert index_only is not None
    only = index_only(fired, TRADE_DATE)
    assert only is not None
    assert {k.underlying_symbol for k in only.instruments} == {"SX5E"}


def test_no_banked_membership_is_a_loud_failure_not_a_silent_index_only_capture(
    tmp_path: Any,
) -> None:
    # The 2026-06-15 canary's exact gap: scope is index+constituents but the store has NO banked
    # 1A membership weights. The OLD behaviour captured the index leg only and exited cleanly
    # (silent) — the bug. It must now RAISE a CRITICAL ConstituentLaneError naming the missing
    # input, so the runner exits non-zero and OnFailure= alerts fire.
    empty = ParquetStore(tmp_path)
    with pytest.raises(ConstituentLaneError, match="no banked 1A membership weights"):
        collect_index_and_constituents_basket(
            _FakeGateway(),
            store=empty,
            index=SX5E,
            as_of=CLOSE,
            next_open=NEXT_OPEN,
            config=_config(3),
            selection=_selection(),
        )


def test_a_missing_weight_basket_is_a_loud_membership_ranking_failure(tmp_path: Any) -> None:
    # Membership present but unrankable (a labeled-unavailable weight): the shared resolver raises
    # MembershipRankingError (loud), never a quietly-truncated top-N. (b) of the fail-loud cases.
    partial = ParquetStore(tmp_path)
    ingest_membership_changes(
        partial,
        [
            MembershipChange("SX5E", "ASML", KNOWN, None, KNOWN, VENDOR, 0.40),
            MembershipChange("SX5E", "TTE", KNOWN, None, KNOWN, VENDOR, None),  # unknown weight
        ],
    )
    with pytest.raises(MembershipRankingError, match="cannot rank a basket"):
        collect_index_and_constituents_basket(
            _FakeGateway(),
            store=partial,
            index=SX5E,
            as_of=CLOSE,
            next_open=NEXT_OPEN,
            config=_config(2),
            selection=_selection(),
        )


def test_capture_attempts_all_resolved_constituents_and_records_one_ledger_row_each(
    store: ParquetStore,
) -> None:
    # Activation + ledger: a top-5 over the 5-name basket must ATTEMPT every name and persist
    # exactly one labelled outcome row per attempted name. Independently derived from the fixture:
    #   ASML/TTE/SIE -> captured (chains listed), SAN1 -> captured (pinned chain),
    #   ENEL -> no_options (the gateway lists none). No unresolved/unentitled here.
    collect_index_and_constituents_basket(
        _FakeGateway(),
        store=store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(5),
        selection=_selection(),
    )
    rows = store.read("constituent_capture_outcomes", trade_date=TRADE_DATE)
    by_name = {row.underlying: row for row in rows}
    assert set(by_name) == {"ASML", "TTE", "SIE", "SAN1", "ENEL"}  # all 5 attempted
    assert {name: row.outcome for name, row in by_name.items()} == {
        "ASML": "captured",
        "TTE": "captured",
        "SIE": "captured",
        "SAN1": "captured",
        "ENEL": "no_options",
    }
    # The captured names carry the full listed ladder (3 strikes × 2 rights × 1 month = 6 legs);
    # the no_options name carries zero, and only a captured outcome carries a non-zero count.
    assert by_name["ASML"].n_options == len(_STRIKES) * 2 * len(_MONTHS)
    assert by_name["ENEL"].n_options == 0
    # Rank is the 1-based weight order: ASML(.40)=1, TTE(.25)=2, SIE(.20)=3, SAN1(.10)=4, ENEL(.05)=5.
    assert {name: row.outcome for name, row in by_name.items()} and by_name["ASML"].rank == 1
    assert by_name["ENEL"].rank == 5
    assert by_name["SAN1"].rank == 4
    # Each name's ledger row lands under its own ``underlying=<SYMBOL>`` partition (Done criteria).
    partitions = {
        p.parent.name
        for p in store.root.rglob("constituent_capture_outcomes/**/*.parquet")
    }
    assert partitions == {f"underlying={name}" for name in by_name}


def test_an_unresolved_constituent_is_recorded_not_silently_dropped(tmp_path: Any) -> None:
    # A name the gateway does not list (no STK conid, not pinned) must land an `unresolved` ledger
    # row — never a silent drop. GHOST is the heaviest so it is unambiguously inside the top-N.
    store = ParquetStore(tmp_path)
    ingest_membership_changes(
        store,
        [
            MembershipChange("SX5E", "GHOST", KNOWN, None, KNOWN, VENDOR, 0.60),
            MembershipChange("SX5E", "ASML", KNOWN, None, KNOWN, VENDOR, 0.40),
        ],
        complete_snapshot=True,
    )
    basket = collect_index_and_constituents_basket(
        _FakeGateway(),
        store=store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(2),
        selection=_selection(),
    )
    assert basket is not None
    # ASML still captured (one bad name never aborts the fire); GHOST omitted from the basket.
    assert {k.underlying_symbol for k in basket.instruments} == {"SX5E", "ASML"}
    by_name = {
        row.underlying: row
        for row in store.read("constituent_capture_outcomes", trade_date=TRADE_DATE)
    }
    assert by_name["GHOST"].outcome == "unresolved"
    assert by_name["ASML"].outcome == "captured"
