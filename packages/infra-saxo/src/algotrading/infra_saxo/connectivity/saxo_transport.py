"""Saxo Bank OpenAPI transport: REST (httpx) + WebSocket (websockets).

This module handles the wire layer only — authentication headers, JSON parsing,
and WebSocket frame routing. No discovery logic or market-data semantics live here.
``SaxoTransport`` is intentionally separate from the IBKR session lifecycle because
Saxo uses stateless REST calls rather than a persistent TWS socket.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from algotrading.core.log import get_logger

_log = get_logger(__name__)

_DEFAULT_TIMEOUT_S = 15.0


class SaxoTransportError(Exception):
    """Raised when a Saxo REST call or WebSocket operation fails."""


class SaxoTransport:
    """REST + WebSocket transport for the Saxo Bank OpenAPI.

    Caller provides a ``token_fn`` that returns the current Bearer token so the
    transport stays stateless with respect to auth — token rotation is handled
    externally by ``TokenManager``.

    ``base_url`` defaults to the simulation gateway; override with the live URL for
    production use.
    """

    SIM_BASE_URL = "https://gateway.saxobank.com/sim/openapi"
    LIVE_BASE_URL = "https://gateway.saxobank.com/openapi"

    def __init__(
        self,
        *,
        token_fn: Callable[[], str],
        base_url: str = SIM_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT_S,
        _client: httpx.Client | None = None,
    ) -> None:
        self._token_fn = token_fn
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Allow injection of a mock client for tests
        self._client = _client or httpx.Client(timeout=timeout)

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` with optional query params; return the parsed JSON body.

        Raises SaxoTransportError on non-2xx HTTP status or network failure.
        """
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token_fn()}"}
        try:
            resp = self._client.get(url, params=params or {}, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise SaxoTransportError(
                f"Saxo REST {exc.response.status_code} for {path}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise SaxoTransportError(f"Saxo REST call failed for {path}: {exc}") from exc

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST JSON ``body`` to ``path``; return the parsed JSON body."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._token_fn()}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._client.post(url, content=json.dumps(body), headers=headers)
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except httpx.HTTPStatusError as exc:
            raise SaxoTransportError(
                f"Saxo REST POST {exc.response.status_code} for {path}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise SaxoTransportError(f"Saxo REST POST failed for {path}: {exc}") from exc

    def patch(self, path: str, body: dict[str, Any]) -> None:
        """PATCH ``body`` to ``path`` (e.g. to update a streaming subscription window)."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._token_fn()}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._client.patch(url, content=json.dumps(body), headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SaxoTransportError(
                f"Saxo REST PATCH {exc.response.status_code} for {path}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise SaxoTransportError(f"Saxo REST PATCH failed for {path}: {exc}") from exc

    def delete(self, path: str) -> None:
        """DELETE ``path`` (e.g. to remove a streaming subscription)."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token_fn()}"}
        try:
            resp = self._client.delete(url, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SaxoTransportError(
                f"Saxo REST DELETE {exc.response.status_code} for {path}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise SaxoTransportError(f"Saxo REST DELETE failed for {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # WebSocket (synchronous wrapper via asyncio — called from the adapter)
    # ------------------------------------------------------------------

    def streaming_url(self, context_id: str) -> str:
        """The streaming WebSocket connect URL for the current environment.

        The streaming service has its own host and path — distinct from the REST gateway and from
        each other per environment (live ``live-streaming.saxobank.com/oapi/streaming/ws``, sim
        ``sim-streaming.saxobank.com/sim/oapi/streaming/ws``). Saxo requires the subscription's
        ``contextId`` as a mandatory query-string parameter; the handshake is rejected without the
        exact host, path, and contextId.
        """
        if "/sim/" in self._base_url:
            connect = "wss://sim-streaming.saxobank.com/sim/oapi/streaming/ws/connect"
        else:
            connect = "wss://live-streaming.saxobank.com/oapi/streaming/ws/connect"
        return f"{connect}?contextId={context_id}"

    def auth_header(self) -> dict[str, str]:
        """Current Bearer header dict for use in WS connection handshake."""
        return {"Authorization": f"Bearer {self._token_fn()}"}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()
