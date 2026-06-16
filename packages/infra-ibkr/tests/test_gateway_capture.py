from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StrikeSelectionConfig,
    UniverseConfig,
)
from algotrading.infra.orchestration.eod_runner import FiredIndex
from algotrading.infra.universe import ChainSelection, IndexRegistry, parse_index_registry
from algotrading.infra_ibkr.live_capture import gateway_basket_source
from algotrading.infra_ibkr.session_factory import build_gateway_session
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG

TRADE_DATE = date(2026, 3, 12)
SPX_CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
SPX_NEXT_OPEN = datetime(2026, 3, 13, 13, 30, tzinfo=UTC)
INDEX_CONID = 416904
_SPOT = 100.0
_MONTH = "JUN26"
_EXPIRY = date(2026, 6, 10)
_STRIKES = (90.0, 95.0, 100.0, 105.0, 110.0)


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(
            version="u-1", exchange="CBOE",
            strike_selection=StrikeSelectionConfig(version="ss-1"),
        ),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,)),
    )


def _registry() -> IndexRegistry:
    return parse_index_registry(
        {
            "SPX": {
                "name": "S&P 500", "calendar": "XNYS", "currency": "USD",
                "ibkr": {"conid": 0, "secType": "IND", "exchange": "CBOE"},
                "enabled": True,
            }
        }
    )


def _conid_for(strike: float, right: str) -> int:
    return 2_000_000 + int(strike) * 2 + (0 if right == "C" else 1)


def _mark(strike: float, right: str) -> float:
    intrinsic = max(_SPOT - strike, 0.0) if right == "C" else max(strike - _SPOT, 0.0)
    return intrinsic + 3.0


class _FakeMarketGateway:

    def __init__(self) -> None:
        self._close_ms = int(SPX_CLOSE.timestamp() * 1000)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        if path == "/iserver/secdef/search":
            return [
                {
                    "conid": INDEX_CONID, "symbol": "SPX",
                    "sections": [
                        {"secType": "IND", "exchange": "CBOE"},
                        {"secType": "OPT", "months": _MONTH, "exchange": "CBOE"},
                    ],
                }
            ]
        if path == "/iserver/secdef/strikes":
            return {"call": list(_STRIKES), "put": list(_STRIKES)}
        if path == "/iserver/secdef/info":
            strike, right = float(params["strike"]), str(params["right"])
            return [
                {
                    "conid": str(_conid_for(strike, right)),
                    "maturityDate": _EXPIRY.strftime("%Y%m%d"),
                    "strike": str(strike), "right": right,
                }
            ]
        if path == "/iserver/marketdata/snapshot":
            return self._snapshot(params)
        raise AssertionError(f"unexpected path {path!r}")

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows: list[dict[str, Any]] = []
        for conid_text in str(params["conids"]).split(","):
            conid = int(conid_text)
            if conid == INDEX_CONID:
                rows.append({"conid": conid, "31": str(_SPOT), "_updated": self._close_ms})
                continue
            mark = self._mark_for_conid(conid)
            rows.append(
                {"conid": conid, "31": f"{mark:.4f}", "84": f"{mark - 0.1:.4f}",
                 "86": f"{mark + 0.1:.4f}", "_updated": self._close_ms}
            )
        return rows

    def _mark_for_conid(self, conid: int) -> float:
        for strike in _STRIKES:
            for right in ("C", "P"):
                if _conid_for(strike, right) == conid:
                    return _mark(strike, right)
        raise AssertionError(f"no mark for conid {conid}")


class _FakeGateway:

    def __init__(self, *, established: bool = True) -> None:
        self._established = established
        self._market = _FakeMarketGateway()

    def _status(self) -> dict[str, Any]:
        return {
            "authenticated": self._established, "competing": False,
            "connected": self._established, "established": self._established,
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/iserver/auth/status":
            return self._status()
        return self._market.get(path, params)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        if path == "/iserver/auth/ssodh/init":
            return self._status()
        if path == "/tickle":
            return {"iserver": {"authStatus": self._status()}}
        raise AssertionError(f"unexpected post {path!r}")


_SELECTION = ChainSelection(max_expiries=1, min_strikes_per_side=5, option_exchange="CBOE")


def test_gateway_not_requested_returns_none() -> None:
    assert gateway_basket_source(env={}) is None


def test_gateway_requested_binds_and_captures() -> None:
    source = gateway_basket_source(
        env={"IBKR_CP_GATEWAY": "1"},
        transport=_FakeGateway(),
        config=_config(),
        selection=_SELECTION,
        now=lambda: TRADE_DATE,
    )
    assert source is not None

    fired = FiredIndex(entry=_registry().get("SPX"), as_of=SPX_CLOSE, next_open=SPX_NEXT_OPEN)
    basket = source(fired, TRADE_DATE, "test-corr")
    assert basket is not None and basket.events, "the requested same-day Gateway fire must capture"
    assert len(basket.instruments) >= 2
    assert {event.exchange_ts for event in basket.events} == {SPX_CLOSE}


def test_build_gateway_session_establishes_over_fake_gateway() -> None:
    transport = _FakeGateway(established=True)
    built = build_gateway_session(
        env={"IBKR_CP_GATEWAY": "1"}, transport=transport
    )
    bound_transport, session = built
    assert bound_transport is transport
    assert session.established() is True
