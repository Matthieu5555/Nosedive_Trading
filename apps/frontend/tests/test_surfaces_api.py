"""Surfaces router tests: empty store, bad date → typed errors, never a 500."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_surfaces_empty_for_unknown_underlying(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/surfaces", params={"underlying": "ZZZZ"}).json()
    assert payload["n_slices"] == 0
    assert payload["slices"] == []


def test_surfaces_empty_for_no_underlying(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/surfaces").json()
    assert payload["n_slices"] == 0
    assert payload["slices"] == []
    assert "underlying" in payload


def test_surfaces_underlyings_empty_on_empty_store(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/surfaces/underlyings").json()
    assert payload["underlyings"] == []


def test_surfaces_bad_trade_date_returns_400(infra_client: TestClient) -> None:
    response = infra_client.get("/api/surfaces", params={"trade_date": "not-a-date"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_trade_date"
