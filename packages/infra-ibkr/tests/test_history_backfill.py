"""IBKR OHLC backfill wiring (WS 1C): credentialed-collector selection + request resolution.

No live Gateway and no secrets: a fake CP REST transport answers ``/iserver/secdef/search`` (index
+ equity conids) and ``/iserver/marketdata/history`` (canned OHLC), and the store is a real
``ParquetStore`` over ``tmp_path`` (the seam is the actual write/read). The obligations:

* a non-credentialed environment yields no collector (``None``) — the clean no-op path;
* request resolution turns the enabled indices into per-ticker requests, resolving the index
  underlying conid (never the registry placeholder) and, by default, the as-of constituents' equity
  conids — each ticker once;
* ``--no-constituents`` resolves the index underlyings only;
* the bound collector fetches + persists a ``DailyBar`` set for every requested ticker.
"""

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

_CALC_TS = datetime(2026, 6, 7, 20, 0, tzinfo=UTC)
_AS_OF = date(2026, 6, 1)

# symbol -> conid the fake search resolves (the registry carries the conid: 0 placeholder, so the
# resolved values below are what proves request resolution does NOT trust the placeholder).
_CONID = {"SX5E": 12345, "ASML": 8001, "SAP": 8002}
_KNOWN = date(2010, 1, 1)
_VENDOR = "test-vendor"

# Two constituents whose snapshot weights sum to 1.0 (the membership ingest validates the sum).
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


class _FakeTransport:
    """Answers secdef-search (index + equity) and marketdata-history over canned data."""

    def __init__(self) -> None:
        self.get_paths: list[str] = []
        self._history = {conid: _ohlc(sym) for sym, conid in _CONID.items()}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.get_paths.append(path)
        params = dict(params or {})
        if path == "/iserver/secdef/search":
            symbol = str(params["symbol"])
            conid = _CONID[symbol]
            if symbol == "SX5E":  # the index: carries a sections block resolve_index matches on
                return [
                    {
                        "conid": conid,
                        "symbol": symbol,
                        "sections": [{"secType": "IND", "exchange": "EUREX"}],
                    }
                ]
            return [{"conid": conid, "symbol": symbol}]  # an equity constituent
        if path == "/iserver/marketdata/history":
            conid = int(params.get("conid", 0))
            return self._history.get(conid, {"data": []})
        raise AssertionError(f"unexpected path {path!r}")

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return None


def _seeded_store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(tmp_path)
    ingest_membership_changes(store, _SX5E_MEMBERS)
    return store


# -- credentialed-collector selection -----------------------------------------------------
def test_build_history_collector_is_none_without_credentials(tmp_path: Path) -> None:
    """An empty environment binds no collector — the clean no-op (nothing to backfill)."""
    collector = build_history_collector(
        store=ParquetStore(tmp_path), calc_ts=_CALC_TS, env={}
    )
    assert collector is None


# -- request resolution: index + constituents ---------------------------------------------
def test_requests_resolve_index_and_constituent_conids(tmp_path: Path) -> None:
    """Each enabled index resolves its underlying conid + its as-of constituents' equity conids."""
    store = _seeded_store(tmp_path)
    requests = history_requests_for(
        store=store,
        registry=_registry(),
        transport=_FakeTransport(),
        period="5y",
        as_of_date=_AS_OF,
    )
    by_symbol = {r.underlying: r.conid for r in requests}
    # The index underlying plus both constituents, each once, with the RESOLVED conids (not the
    # registry's 0 placeholder), all carrying the requested period.
    assert by_symbol == {"SX5E": 12345, "ASML": 8001, "SAP": 8002}
    assert {r.period for r in requests} == {"5y"}
    assert len(requests) == 3  # no duplicate tickers


def test_no_constituents_resolves_index_underlyings_only(tmp_path: Path) -> None:
    """``include_constituents=False`` backfills the index underlyings only (no membership read)."""
    requests = history_requests_for(
        store=ParquetStore(tmp_path),  # no membership seeded — must not be read
        registry=_registry(),
        transport=_FakeTransport(),
        period="5y",
        as_of_date=_AS_OF,
        include_constituents=False,
    )
    assert [r.underlying for r in requests] == ["SX5E"]


# -- end to end: the bound collector fetches + persists DailyBars --------------------------
def test_backfill_persists_daily_bars_for_every_ticker(tmp_path: Path) -> None:
    """A bound collector (injected fake transport) fetches and persists a DailyBar set per ticker."""
    store = _seeded_store(tmp_path)
    transport = _FakeTransport()
    collector = build_history_collector(
        store=store, calc_ts=_CALC_TS, transport=transport
    )
    assert collector is not None

    requests = history_requests_for(
        store=store, registry=_registry(), transport=transport, period="5y", as_of_date=_AS_OF
    )
    result = collector.backfill(requests, correlation_id="ohlc-test")

    assert set(result.fetched) == {"SX5E", "ASML", "SAP"}
    assert result.bar_count == 6  # 2 bars × 3 tickers
    persisted = store.read("daily_bar", underlying=None, provider="IBKR")
    assert {bar.underlying for bar in persisted} == {"SX5E", "ASML", "SAP"}
    # Read-only invariant: the path touches only secdef/search + marketdata/history, never an order.
    assert all(
        p in {"/iserver/secdef/search", "/iserver/marketdata/history"} for p in transport.get_paths
    )
