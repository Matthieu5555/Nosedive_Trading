from __future__ import annotations

from algotrading.frontend.app import create_app
from fastapi.testclient import TestClient


def test_orders_dashboard_returns_paper_state_and_history() -> None:
    client = TestClient(create_app())

    response = client.get("/api/orders")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "paper"
    assert len(body["open_orders"]) == 1
    assert len(body["history"]) >= 2
    assert body["recent_preview"]["paper_only"] is True


def test_order_preview_returns_notional_and_greek_impact() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/orders/preview",
        json={
            "side": "buy",
            "symbol": "SPX",
            "quantity": 3,
            "limit_price": 18.5,
            "instrument_type": "index_option",
            "expiry": "2026-06-19",
            "strike": 5350,
            "option_type": "call",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["estimated_notional"] == 5550.0
    assert body["risk_check"] == "pass"
    assert body["greek_impact"]["delta"] > 0


def test_order_submit_accepts_paper_order() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/orders",
        json={
            "side": "sell",
            "symbol": "SPX",
            "quantity": 1,
            "limit_price": 22.0,
            "instrument_type": "index_option",
            "expiry": "2026-07-17",
            "strike": 5250,
            "option_type": "put",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "paper_accepted"
    assert body["order_id"].startswith("PAPER-")
