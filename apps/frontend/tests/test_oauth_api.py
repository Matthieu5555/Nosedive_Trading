"""OAuth router tests: CSRF lifecycle, replay rejection, and status endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_saxo_start_returns_authorize_url_and_state(infra_client: TestClient) -> None:
    response = infra_client.post("/api/oauth/saxo/start")
    assert response.status_code == 200
    body = response.json()
    assert body["authorize_url"].startswith("https://")
    assert len(body["state"]) > 20  # meaningful-length CSRF token


def test_saxo_callback_with_valid_state_returns_501_not_configured(
    infra_client: TestClient,
) -> None:
    start = infra_client.post("/api/oauth/saxo/start").json()
    response = infra_client.get(
        "/api/oauth/saxo/callback",
        params={"code": "auth_code_abc", "state": start["state"]},
    )
    assert response.status_code == 501
    assert response.json()["error"] == "saxo_backend_not_configured"


def test_saxo_callback_replayed_state_is_rejected(infra_client: TestClient) -> None:
    start = infra_client.post("/api/oauth/saxo/start").json()
    state = start["state"]
    infra_client.get("/api/oauth/saxo/callback", params={"code": "code1", "state": state})
    replay = infra_client.get(
        "/api/oauth/saxo/callback", params={"code": "code1", "state": state}
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_state"


def test_saxo_callback_forged_state_is_rejected(infra_client: TestClient) -> None:
    response = infra_client.get(
        "/api/oauth/saxo/callback",
        params={"code": "code", "state": "forged_token"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_state"


def test_saxo_callback_missing_code_returns_400(infra_client: TestClient) -> None:
    start = infra_client.post("/api/oauth/saxo/start").json()
    response = infra_client.get(
        "/api/oauth/saxo/callback", params={"state": start["state"]}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "missing_code"


def test_saxo_status_reports_not_configured(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/oauth/saxo/status").json()
    assert payload["configured"] is False
    assert payload["authenticated"] is False


def test_saxo_revoke_reports_not_configured(infra_client: TestClient) -> None:
    payload = infra_client.delete("/api/oauth/saxo").json()
    assert payload["revoked"] is False
    assert payload["configured"] is False
