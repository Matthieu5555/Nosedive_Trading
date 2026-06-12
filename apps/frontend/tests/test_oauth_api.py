"""OAuth router tests: CSRF lifecycle, replay rejection, status, and app-lifetime state."""

from __future__ import annotations

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
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


# --------------------------------------------------------------------------- #
# App-lifetime OAuth state (audit M41): per-app CSRF store, env read per app    #
# --------------------------------------------------------------------------- #


def test_csrf_state_is_per_app_not_module_global(ctx: AppContext) -> None:
    # A state minted by one app must not validate on another (the store used to be a
    # module singleton shared by every app in the process).
    with TestClient(create_app(ctx)) as first, TestClient(create_app(ctx)) as second:
        state = first.post("/api/oauth/saxo/start").json()["state"]
        response = second.get(
            "/api/oauth/saxo/callback", params={"code": "code", "state": state}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_state"
        # The minting app still accepts its own token.
        ok = first.get("/api/oauth/saxo/callback", params={"code": "code", "state": state})
        assert ok.status_code == 501  # state valid; backend not configured


def test_saxo_env_is_read_at_app_construction_not_import(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The SAXO_* env vars are read in create_app, so re-pointing the env and building a new
    # app takes effect without re-importing the module (they used to be frozen at import).
    monkeypatch.setenv("SAXO_CLIENT_ID", "client-from-env")
    monkeypatch.setenv("SAXO_AUTHORIZE_URL", "https://example.test/authorize")
    with TestClient(create_app(ctx)) as client:
        body = client.post("/api/oauth/saxo/start").json()
    assert body["authorize_url"].startswith("https://example.test/authorize?")
    assert "client_id=client-from-env" in body["authorize_url"]
