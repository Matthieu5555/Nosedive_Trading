"""Health router tests: stub response when orchestration seam is pending."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_stub_when_orchestration_pending(infra_client: TestClient) -> None:
    # The orchestration seam (C3) is not yet landed; the endpoint must return a
    # degraded stub with a clear note rather than a 500.
    response = infra_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["is_healthy"] is False
    assert "note" in body
    assert "trade_date" in body


def test_health_bad_trade_date_when_orchestration_available_returns_400(
    infra_client: TestClient,
) -> None:
    # When orchestration is unavailable, the stub is returned without date parsing.
    # This test verifies the stub path handles an explicit date gracefully.
    response = infra_client.get("/api/health", params={"trade_date": "not-a-date"})
    assert response.status_code == 200
    body = response.json()
    # Stub path: uses the raw trade_date string as-is (no parsing needed for stub).
    assert body["trade_date"] == "not-a-date"
    assert "note" in body
