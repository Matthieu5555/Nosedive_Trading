from __future__ import annotations

import pytest
from algotrading.frontend.routers import ibkr as ibkr_router
from algotrading.infra_ibkr.connectivity.cp_rest_transport import CpRestTransportError
from fastapi.testclient import TestClient


class _DownTransport:
    """A transport whose every call raises a connection-level CpRestTransportError (no status code).

    Stands in for "nothing answering on the base URL" so the not-configured degrade path is
    exercised deterministically, with no dependency on a live (or shared) gateway process.
    """

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def get(self, _path: str, _params: object = None) -> object:
        raise CpRestTransportError("connect failed")

    def post(self, _path: str, _body: object = None) -> object:
        raise CpRestTransportError("connect failed")

    def close(self) -> None:
        pass


class _AuthenticatedTransport:
    """A transport that answers like a live, authenticated gateway with a brokerage session.

    Stands in for the real Client-Portal gateway being up and logged in. No env var is set, so this
    proves "configured" is decided by a real probe, not by `IBKR_CP_GATEWAY`.
    """

    AUTH_STATUS = {"authenticated": True, "competing": False, "established": True, "connected": True}
    ACCOUNT = "DUQ574355"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.ssodh_calls = 0

    def get(self, path: str, _params: object = None) -> object:
        if path == "/iserver/auth/status":
            return dict(self.AUTH_STATUS)
        if path == "/iserver/accounts":
            return {"accounts": [self.ACCOUNT]}
        raise CpRestTransportError(f"unexpected GET {path}")

    def post(self, path: str, _body: object = None) -> object:
        if path == "/iserver/auth/ssodh/init":
            self.ssodh_calls += 1
            return dict(self.AUTH_STATUS)
        raise CpRestTransportError(f"unexpected POST {path}")

    def close(self) -> None:
        pass


class _Unauthorized401Transport:
    """A transport whose auth probe raises a 401: gateway up, but no SSO session."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def get(self, _path: str, _params: object = None) -> object:
        raise CpRestTransportError("unauthorized", status_code=401)

    def post(self, _path: str, _body: object = None) -> object:
        raise CpRestTransportError("unauthorized", status_code=401)

    def close(self) -> None:
        pass


@pytest.fixture
def gateway_down(monkeypatch: pytest.MonkeyPatch) -> None:
    # Env var explicitly unset: a dead gateway must still degrade cleanly, never 500.
    monkeypatch.delenv("IBKR_CP_GATEWAY", raising=False)
    monkeypatch.setattr(ibkr_router, "CpRestTransport", _DownTransport)


@pytest.fixture
def gateway_live(monkeypatch: pytest.MonkeyPatch) -> _AuthenticatedTransport:
    # Env var deliberately unset: this is the bug the fix closes. A live gateway must be reported as
    # configured/authenticated/established purely from the probe, with no env var set.
    monkeypatch.delenv("IBKR_CP_GATEWAY", raising=False)
    instances: list[_AuthenticatedTransport] = []

    def _factory(*args: object, **kwargs: object) -> _AuthenticatedTransport:
        transport = _AuthenticatedTransport(*args, **kwargs)
        instances.append(transport)
        return transport

    monkeypatch.setattr(ibkr_router, "CpRestTransport", _factory)
    # Return a probe handle so tests can inspect ssodh/init calls on the connect path.
    return instances  # type: ignore[return-value]


@pytest.fixture
def gateway_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IBKR_CP_GATEWAY", raising=False)
    monkeypatch.setattr(ibkr_router, "CpRestTransport", _Unauthorized401Transport)


def test_status_reports_not_configured_when_no_gateway_answers(
    infra_client: TestClient,
    gateway_down: None,
) -> None:
    # Nothing answering on the base URL: a clean 200 with configured:false, never a 500.
    response = infra_client.get("/api/ibkr/status")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["authenticated"] is False
    assert body["established"] is False
    assert body["account"] is None
    # The honest detail carries the env + login instruction so the operator knows the next step.
    assert "IBKR_CP_GATEWAY=1" in body["detail"]
    assert "scripts/ibkr_login.py" in body["detail"]


def test_connect_returns_409_not_configured_when_no_gateway_answers(
    infra_client: TestClient,
    gateway_down: None,
) -> None:
    response = infra_client.post("/api/ibkr/connect")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "ibkr_not_configured"
    assert body["login_hint"] == "! scripts/ibkr_login.py"
    assert "scripts/ibkr_login.py" in body["detail"]


def test_status_reports_live_gateway_without_env_var(
    infra_client: TestClient,
    gateway_live: list[_AuthenticatedTransport],
) -> None:
    # The core fix: env var unset, but a live authenticated gateway answers the probe. Status must
    # reflect reality, not the (absent) flag.
    response = infra_client.get("/api/ibkr/status")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["authenticated"] is True
    assert body["established"] is True
    assert body["account"] == "DUQ574355"


def test_connect_opens_brokerage_session_on_live_gateway_without_env_var(
    infra_client: TestClient,
    gateway_live: list[_AuthenticatedTransport],
) -> None:
    response = infra_client.post("/api/ibkr/connect")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["authenticated"] is True
    assert body["established"] is True
    assert body["account"] == "DUQ574355"
    # ssodh/init was actually invoked (the connect endpoint opens the brokerage session).
    assert any(t.ssodh_calls >= 1 for t in gateway_live)


def test_status_reports_gateway_up_but_no_sso_session_on_401(
    infra_client: TestClient,
    gateway_401: None,
) -> None:
    response = infra_client.get("/api/ibkr/status")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["authenticated"] is False
    assert body["established"] is False
    assert "no SSO session" in body["detail"]
    assert "scripts/ibkr_login.py" in body["detail"]


def test_connect_returns_409_not_authenticated_on_401(
    infra_client: TestClient,
    gateway_401: None,
) -> None:
    response = infra_client.post("/api/ibkr/connect")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "ibkr_not_authenticated"
    assert body["login_hint"] == "! scripts/ibkr_login.py"
    assert "scripts/ibkr_login.py" in body["detail"]
