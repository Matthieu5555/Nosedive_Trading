"""Deribit REST and WebSocket transport — the network edge for the crypto adapter.

Provides two surfaces:
- ``DeribitTransport.get`` — synchronous REST call via httpx (discovery, instrument lookup).
- ``DeribitTransport.ws_listener`` — a started-on-demand ``WebSocketListener`` (owned thread,
  stop event, reconnect with backoff) that subscribes to channels and pushes each parsed
  notification frame to a callback (live ticks).

Both surfaces use only public endpoints; no authentication is required for market data.
``DeribitSession`` is a thin context manager that owns the transport lifecycle and exposes
the transport for callers that need it. The push ``DeribitMarketDataAdapter`` drives this
transport and feeds ticks to the one unified ``RawCollector`` (ADR 0027); there is no pull
tick loop here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from algotrading.core.log import get_logger

from .ws_listener import WebSocketListener

_log = get_logger(__name__)

_DEFAULT_REST_BASE = "https://www.deribit.com/api/v2"
_DEFAULT_WS_BASE = "wss://www.deribit.com/ws/api/v2"


class DeribitTransport:
    """Low-level Deribit transport: REST GET and WebSocket subscribe.

    Keeps the broker URL configurable so tests can inject a mock base URL or
    point at the Deribit testnet (``https://test.deribit.com/api/v2``).
    """

    def __init__(
        self,
        *,
        rest_base: str = _DEFAULT_REST_BASE,
        ws_base: str = _DEFAULT_WS_BASE,
        client: httpx.Client | None = None,
    ) -> None:
        self._rest_base = rest_base.rstrip("/")
        self._ws_base = ws_base.rstrip("/")
        # Allow injection of a pre-configured client (e.g. httpx.MockTransport in tests).
        self._client = client or httpx.Client(timeout=10.0)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue a GET request to a public Deribit REST endpoint.

        ``path`` must start with ``/`` (e.g. ``/public/get_instruments``).
        Returns the ``result`` field of the JSON response on success.
        Raises ``httpx.HTTPStatusError`` on 4xx/5xx and ``RuntimeError`` when
        the Deribit envelope carries an ``error`` field.
        """
        url = f"{self._rest_base}{path}"
        _log.debug("deribit_rest_get", extra={"url": url, "params": params})
        response = self._client.get(url, params=params or {})
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        if "error" in body:
            raise RuntimeError(f"Deribit API error: {body['error']}")
        return body.get("result", body)

    def ws_listener(
        self,
        channels: list[str],
        on_message: Callable[[dict[str, Any]], None],
        *,
        on_fault: Callable[[str], None] | None = None,
    ) -> WebSocketListener:
        """Build a (not yet started) reconnecting listener for the given Deribit channels.

        The caller owns the lifecycle: ``listener.start()`` spawns the owned thread,
        ``listener.stop()`` joins it. On every (re)connect the subscribe message is resent,
        so a dropped connection resumes the same channels. Each ``on_message`` call receives
        the parsed JSON payload of one notification frame; subscription confirmations
        (frames without ``method``) are skipped. ``on_fault`` receives a reason string for
        each connection loss or fatal error (the listener keeps reconnecting).
        """
        subscribe_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "public/subscribe",
                "params": {"channels": channels},
            }
        )

        def _connect_factory() -> object:
            # Import here so the sync path (REST-only) never requires websockets installed.
            import websockets

            _log.info("deribit_ws_connect", extra={"url": self._ws_base, "channels": channels})
            return websockets.connect(self._ws_base)

        async def _send_subscribe(ws: Any) -> None:
            await ws.send(subscribe_msg)

        def _on_frame(raw: bytes | str) -> None:
            frame: dict[str, Any] = json.loads(raw)
            # Deribit sends subscription confirmations on id=1; skip them.
            if "method" not in frame:
                return
            on_message(frame)

        return WebSocketListener(
            connect_factory=_connect_factory,
            on_frame=_on_frame,
            on_connect=_send_subscribe,
            on_fault=on_fault,
            name="deribit-ws-listener",
        )

    def close(self) -> None:
        """Release the underlying HTTP client."""
        self._client.close()


class DeribitSession:
    """Context manager that owns a ``DeribitTransport`` lifecycle.

    Usage::

        with DeribitSession() as session:
            result = session.transport.get("/public/get_instruments", {"currency": "BTC"})
    """

    def __init__(self, **transport_kwargs: Any) -> None:
        self._kwargs = transport_kwargs
        self.transport: DeribitTransport | None = None

    def __enter__(self) -> DeribitSession:
        self.transport = DeribitTransport(**self._kwargs)
        return self

    def __exit__(self, *_: object) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None


@contextmanager
def deribit_session(**transport_kwargs: Any) -> Iterator[DeribitSession]:
    """Functional context manager alias for ``DeribitSession``."""
    session = DeribitSession(**transport_kwargs)
    with session:
        yield session
