from __future__ import annotations

import threading
import time
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
from algotrading.infra_ibkr.connectivity.cp_rest_transport import CpRestTransportError
from algotrading.infra_ibkr.live_capture import live_basket_source
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG

SX5E = IndexEntry(
    "SX5E",
    "EURO STOXX 50",
    "XEUR",
    "EUR",
    IbkrRef(0, "IND", "EUREX", symbol="ESTX50", constituent_conids=(("SAN1", 29612249),)),
    True,
)
CLOSE = datetime(2026, 3, 12, 16, 30, tzinfo=UTC)
NEXT_OPEN = datetime(2026, 3, 13, 7, 0, tzinfo=UTC)
TRADE_DATE = date(2026, 3, 12)
KNOWN = date(2026, 1, 1)
VENDOR = "Test"

INDEX_CONID = 320227571
_MONTHS = {"JUN26": date(2026, 6, 19)}
_STRIKES = (95.0, 100.0, 105.0)
_SPOT = 100.0

_EQUITY_CONID = {"ASML": 600001, "TTE": 600002, "SIE": 600003}
_WEIGHTS = {"ASML": 0.40, "TTE": 0.25, "SIE": 0.20, "SAN1": 0.10, "ENEL": 0.05}
_NO_OPTION_NAMES = {"ENEL"}
_ENEL_CONID = 600004


def _config(
    top_n: int, *, capture_pool_size: int = 6, discovery_pool_size: int = 6
) -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(
            version="u-1",
            exchange="EUREX",
            tenor_grid=("1m", "3m"),
            strike_selection=StrikeSelectionConfig(
                version="ss-1",
                min_strikes_per_side=3,
                capture_pool_size=capture_pool_size,
                discovery_pool_size=discovery_pool_size,
            ),
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
    base = underlying_conid * 1_000_000 + int(expiry.strftime("%y%m%d")) * 100
    return base + int(strike) * 2 + (0 if right == "C" else 1)


def _close_mark(strike: float, right: str) -> float:
    intrinsic = max(_SPOT - strike, 0.0) if right == "C" else max(strike - _SPOT, 0.0)
    return intrinsic + 3.0


class _FakeGateway:

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
        if sec_type == "STK":
            assert symbol != "SAN1", "pinned SAN1 conid must come from the pin, never a ticker search"
            self.stk_searches.append(symbol)
            conid = {**_EQUITY_CONID, "ENEL": _ENEL_CONID}.get(symbol)
            if conid is None:
                return []
            return [{"conid": conid, "symbol": symbol, "sections": [{"secType": "STK"}]}]
        return self._option_months_search(symbol)

    def _option_months_search(self, symbol: str) -> Any:
        conid = {**_EQUITY_CONID, "ENEL": _ENEL_CONID, "SAN1": 29612249}.get(symbol)
        if conid is None:
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
            if conid in self._conid_to_underlying:
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


def test_capture_widens_to_the_top_n_constituents_by_weight(store: ParquetStore) -> None:
    basket = _capture(store, top_n=3)
    assert basket is not None
    underlyings = {k.underlying_symbol for k in basket.instruments}
    assert underlyings == {"SX5E", "ASML", "TTE", "SIE"}
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
    assert index_leg.broker_contract_id == str(INDEX_CONID)


def test_pinned_constituent_is_fetched_by_conid_and_only_inside_its_top_n(
    store: ParquetStore,
) -> None:
    top3 = _capture(store, top_n=3)
    assert top3 is not None
    assert "SAN1" not in {k.underlying_symbol for k in top3.instruments}
    top4 = _capture(store, top_n=4)
    assert top4 is not None
    san1_legs = [k for k in top4.instruments if k.underlying_symbol == "SAN1"]
    assert san1_legs
    san1_underlying = next(k for k in san1_legs if not k.is_option())
    assert san1_underlying.broker_contract_id == "29612249"


def test_a_constituent_with_no_listed_options_is_a_clean_per_name_skip(
    store: ParquetStore,
) -> None:
    basket = _capture(store, top_n=5)
    assert basket is not None
    underlyings = {k.underlying_symbol for k in basket.instruments}
    assert "ENEL" not in underlyings
    assert underlyings == {"SX5E", "ASML", "TTE", "SIE", "SAN1"}


def test_constituent_events_carry_the_same_session_close_as_the_index(
    store: ParquetStore,
) -> None:
    basket = _capture(store, top_n=3)
    assert basket is not None
    assert {e.canonical_ts for e in basket.events} == {CLOSE}
    assert {e.trade_date for e in basket.events} == {TRADE_DATE}
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
    partial = ParquetStore(tmp_path)
    ingest_membership_changes(
        partial,
        [
            MembershipChange("SX5E", "ASML", KNOWN, None, KNOWN, VENDOR, 0.40),
            MembershipChange("SX5E", "TTE", KNOWN, None, KNOWN, VENDOR, None),
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
    assert set(by_name) == {"ASML", "TTE", "SIE", "SAN1", "ENEL"}
    assert {name: row.outcome for name, row in by_name.items()} == {
        "ASML": "captured",
        "TTE": "captured",
        "SIE": "captured",
        "SAN1": "captured",
        "ENEL": "no_options",
    }
    assert by_name["ASML"].n_options == len(_STRIKES) * 2 * len(_MONTHS)
    assert by_name["ENEL"].n_options == 0
    assert {name: row.outcome for name, row in by_name.items()} and by_name["ASML"].rank == 1
    assert by_name["ENEL"].rank == 5
    assert by_name["SAN1"].rank == 4
    partitions = {
        p.parent.name
        for p in store.root.rglob("constituent_capture_outcomes/**/*.parquet")
    }
    assert partitions == {f"underlying={name}" for name in by_name}


def _bank_membership(store: ParquetStore) -> None:
    ingest_membership_changes(
        store,
        [
            MembershipChange("SX5E", name, KNOWN, None, KNOWN, VENDOR, weight)
            for name, weight in _WEIGHTS.items()
        ],
        complete_snapshot=True,
    )


def test_concurrent_capture_is_byte_identical_to_the_serial_capture(tmp_path: Any) -> None:
    serial_store = ParquetStore(tmp_path / "serial")
    concurrent_store = ParquetStore(tmp_path / "concurrent")
    _bank_membership(serial_store)
    _bank_membership(concurrent_store)

    serial = collect_index_and_constituents_basket(
        _FakeGateway(),
        store=serial_store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(5, capture_pool_size=1),
        selection=_selection(),
    )
    concurrent = collect_index_and_constituents_basket(
        _FakeGateway(),
        store=concurrent_store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(5, capture_pool_size=4),
        selection=_selection(),
    )
    assert serial is not None and concurrent is not None
    assert serial.instruments == concurrent.instruments
    assert serial.events == concurrent.events
    assert serial.masters == concurrent.masters
    serial_rows = sorted(
        serial_store.read("constituent_capture_outcomes", trade_date=TRADE_DATE),
        key=lambda r: r.rank,
    )
    concurrent_rows = sorted(
        concurrent_store.read("constituent_capture_outcomes", trade_date=TRADE_DATE),
        key=lambda r: r.rank,
    )
    assert serial_rows == concurrent_rows
    assert [r.outcome for r in serial_rows] == [
        "captured",
        "captured",
        "captured",
        "captured",
        "no_options",
    ]


class _ThrottlingGateway(_FakeGateway):
    def __init__(self, *, throttle_conid: int, fail_first: int) -> None:
        super().__init__()
        self._throttle_conid = throttle_conid
        self._fail_first = fail_first
        self._lock = threading.Lock()
        self.strike_calls = 0

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        if path == "/iserver/secdef/strikes" and int(params.get("conid", 0)) == self._throttle_conid:
            with self._lock:
                self.strike_calls += 1
                throttle = self.strike_calls <= self._fail_first
            if throttle:
                raise CpRestTransportError("429 Too Many Requests", status_code=429)
        return super().get(path, params)


def test_a_transient_429_is_recovered_by_the_throttle_sweep_never_no_options(
    store: ParquetStore,
) -> None:
    gateway = _ThrottlingGateway(throttle_conid=_EQUITY_CONID["TTE"], fail_first=1)
    collect_index_and_constituents_basket(
        gateway,
        store=store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(5),
        selection=_selection(),
    )
    by_name = {
        row.underlying: row
        for row in store.read("constituent_capture_outcomes", trade_date=TRADE_DATE)
    }
    assert gateway.strike_calls >= 2
    assert by_name["TTE"].outcome == "captured"
    assert by_name["TTE"].n_options == len(_STRIKES) * 2 * len(_MONTHS)


def test_a_persistent_429_is_recorded_as_throttled_never_no_options(
    store: ParquetStore,
) -> None:
    gateway = _ThrottlingGateway(throttle_conid=_EQUITY_CONID["SIE"], fail_first=10_000)
    collect_index_and_constituents_basket(
        gateway,
        store=store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(5),
        selection=_selection(),
    )
    by_name = {
        row.underlying: row
        for row in store.read("constituent_capture_outcomes", trade_date=TRADE_DATE)
    }
    assert by_name["SIE"].outcome == "throttled"
    assert by_name["SIE"].outcome != "no_options"
    assert by_name["SIE"].n_options == 0
    assert by_name["ASML"].outcome == "captured"


class _ConcurrencyTrackingGateway(_FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            time.sleep(0.01)
            return super().get(path, params)
        finally:
            with self._lock:
                self._in_flight -= 1


def test_one_shared_budget_bounds_total_concurrency_and_overlaps_underlyings(
    store: ParquetStore,
) -> None:
    gw = _ConcurrencyTrackingGateway()
    basket = collect_index_and_constituents_basket(
        gw,
        store=store,
        index=SX5E,
        as_of=CLOSE,
        next_open=NEXT_OPEN,
        config=_config(5, capture_pool_size=3, discovery_pool_size=1),
        selection=_selection(),
    )
    assert basket is not None
    assert gw.max_in_flight <= 3
    assert gw.max_in_flight >= 2


def test_an_unresolved_constituent_is_recorded_not_silently_dropped(tmp_path: Any) -> None:
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
    assert {k.underlying_symbol for k in basket.instruments} == {"SX5E", "ASML"}
    by_name = {
        row.underlying: row
        for row in store.read("constituent_capture_outcomes", trade_date=TRADE_DATE)
    }
    assert by_name["GHOST"].outcome == "unresolved"
    assert by_name["ASML"].outcome == "captured"
