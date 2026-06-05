from __future__ import annotations

from algotrading.frontend.app import create_app
from fastapi.testclient import TestClient


def test_market_dashboard_returns_spx_snapshots_options_greeks_and_surface() -> None:
    client = TestClient(create_app())

    response = client.get("/api/market")

    assert response.status_code == 200
    body = response.json()
    assert body["underlying"]["symbol"] == "SPX"
    assert body["index_snapshot"]["bid"] < body["index_snapshot"]["ask"]
    assert len(body["stock_snapshots"]) >= 6
    assert len(body["option_chain"]) >= 20
    first_option = body["option_chain"][0]
    assert first_option["bid"] < first_option["ask"]
    assert set(first_option["greeks"]) == {"delta", "gamma", "vega", "theta", "rho"}
    assert body["greek_totals"]["vega"] > 0
    assert len(body["volatility_surface"]["points"]) >= 20
    assert body["provenance"]["stamp_hash"]


def test_underlying_selector_contract_includes_default_spx() -> None:
    client = TestClient(create_app())

    response = client.get("/api/underlyings")

    assert response.status_code == 200
    symbols = [item["symbol"] for item in response.json()["underlyings"]]
    assert symbols[0] == "SPX"
    assert "NDX" in symbols


def test_market_unknown_underlying_returns_typed_404_payload() -> None:
    client = TestClient(create_app())

    response = client.get("/api/market?underlying=UNKNOWN")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown underlying: UNKNOWN"}
