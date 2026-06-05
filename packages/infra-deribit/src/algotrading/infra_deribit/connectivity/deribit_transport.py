"""Deribit REST and WebSocket transport — the network edge for the crypto adapter.

Provides two surfaces:
- ``DeribitTransport.get`` — synchronous REST call via httpx (discovery, instrument lookup).
- ``DeribitTransport.subscribe_ws`` — async WebSocket subscription via websockets (live ticks).

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

    async def subscribe_ws(
        self,
        channels: list[str],
        on_message: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to Deribit WebSocket channels and call ``on_message`` for each update.

        Runs until cancelled (asyncio). Each ``on_message`` call receives the parsed
        JSON payload of one notification frame. Subscription errors are logged and
        re-raised so callers can decide whether to reconnect.
        """
        # Import here so the sync path (REST-only) never requires websockets installed.
        import websockets

        subscribe_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "public/subscribe",
                "params": {"channels": channels},
            }
        )
        _log.info("deribit_ws_connect", extra={"url": self._ws_base, "channels": channels})
        async with websockets.connect(self._ws_base) as ws:
            await ws.send(subscribe_msg)
            async for raw in ws:
                frame: dict[str, Any] = json.loads(raw)
                # Deribit sends subscription confirmations on id=1; skip them.
                if "method" not in frame:
                    continue
                on_message(frame)

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
