from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import parse_index_registry
from algotrading.infra.universe.membership import MembershipChange, ingest_membership_changes
from algotrading.infra_ibkr.history_backfill import (
    build_history_collector,
    history_requests_for,
)

from .conftest import FakeCpTransport

_CALC_TS = datetime(2026, 6, 7, 20, 0, tzinfo=UTC)
_AS_OF = date(2026, 6, 1)

_CONID = {"SX5E": 12345, "ASML": 8001, "SAP": 8002}
_KNOWN = date(2010, 1, 1)
_VENDOR = "test-vendor"

_SX5E_MEMBERS = (
    MembershipChange("SX5E", "ASML", _KNOWN, None, _KNOWN, _VENDOR, 0.5),
    MembershipChange("SX5E", "SAP", _KNOWN, None, _KNOWN, _VENDOR, 0.5),
)


def _registry() -> Any:
    return parse_index_registry(
        {
            "SX5E": {
                "name": "EURO STOXX 50",
                "calendar": "XEUR",
                "currency": "EUR",
                "ibkr": {"conid": 0, "secType": "IND", "exchange": "EUREX"},
                "enabled": True,
            }
        }
    )


def _ohlc(symbol: str) -> dict[str, Any]:
    t0 = int((datetime(2026, 6, 4, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)
    t1 = int((datetime(2026, 6, 5, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)
    return {
        "symbol": symbol,
        "data": [
            {"t": t0, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.25, "v": 1_000_000},
            {"t": t1, "o": 100.25, "h": 102.0, "l": 99.75, "c": 101.5, "v": 2_000_000},
        ],
    }


def _backfill_transport() -> FakeCpTransport:
    history = {conid: _ohlc(sym) for sym, conid in _CONID.items()}

    def _route(path: str, params: dict[str, Any]) -> Any:
        if path == "/iserver/secdef/search":
            symbol = str(params["symbol"])
            conid = _CONID[symbol]
            if symbol == "SX5E":
                return [
                    {
                        "conid": conid,
                        "symbol": symbol,
                        "sections": [{"secType": "IND", "exchange": "EUREX"}],
                    }
                ]
            return [{"conid": conid, "symbol": symbol}]
        if path == "/iserver/marketdata/history":
            return history.get(int(params.get("conid", 0)), {"data": []})
        raise AssertionError(f"unexpected path {path!r}")

    return FakeCpTransport(get_responder=_route, post_response=None)


def _seeded_store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(tmp_path)
    ingest_membership_changes(store, _SX5E_MEMBERS)
    return store


def test_build_history_collector_is_none_without_credentials(tmp_path: Path) -> None:
    collector = build_history_collector(
        store=ParquetStore(tmp_path), calc_ts=_CALC_TS, env={}
    )
    assert collector is None


def test_requests_resolve_index_and_constituent_conids(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path)
    requests = history_requests_for(
        store=store,
        registry=_registry(),
        transport=_backfill_transport(),
        period="5y",
        as_of_date=_AS_OF,
    )
    by_symbol = {r.underlying: r.conid for r in requests}
    assert by_symbol == {"SX5E": 12345, "ASML": 8001, "SAP": 8002}
    assert {r.period for r in requests} == {"5y"}
    assert len(requests) == 3


def test_pinned_constituent_conid_is_resolved_without_a_search(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path)
    registry = parse_index_registry(
        {
            "SX5E": {
                "name": "EURO STOXX 50", "calendar": "XEUR", "currency": "EUR",
                "ibkr": {
                    "conid": 0, "secType": "IND", "exchange": "EUREX",
                    "constituent_conids": {"SAN1": 29612249},
                },
                "enabled": True,
            }
        }
    )
    transport = _backfill_transport()
    requests = history_requests_for(
        store=store, registry=registry, transport=transport, period="5y", as_of_date=_AS_OF
    )
    by_symbol = {r.underlying: r.conid for r in requests}
    assert by_symbol == {"SX5E": 12345, "ASML": 8001, "SAP": 8002, "SAN1": 29612249}
    assert "SAN1" not in _CONID


def test_no_constituents_resolves_index_underlyings_only(tmp_path: Path) -> None:
    requests = history_requests_for(
        store=ParquetStore(tmp_path),
        registry=_registry(),
        transport=_backfill_transport(),
        period="5y",
        as_of_date=_AS_OF,
        include_constituents=False,
    )
    assert [r.underlying for r in requests] == ["SX5E"]


def test_backfill_persists_daily_bars_for_every_ticker(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path)
    transport = _backfill_transport()
    collector = build_history_collector(
        store=store, calc_ts=_CALC_TS, transport=transport
    )
    assert collector is not None

    requests = history_requests_for(
        store=store, registry=_registry(), transport=transport, period="5y", as_of_date=_AS_OF
    )
    result = collector.backfill(requests, correlation_id="ohlc-test")

    assert set(result.fetched) == {"SX5E", "ASML", "SAP"}
    assert result.bar_count == 6
    persisted = store.read("daily_bar", underlying=None, provider="IBKR")
    assert {bar.underlying for bar in persisted} == {"SX5E", "ASML", "SAP"}
    assert all(
        p in {"/iserver/secdef/search", "/iserver/marketdata/history"} for p in transport.get_paths
    )
