"""Lifecycle tests for the Deribit WS path on the shared listener runner.

The full WebSocketListener behavior suite lives in infra (`tests/test_ws_listener.py`)
against the canonical ``algotrading.infra.collectors.ws_listener``; here we pin (a) that the
leaf import path re-exports that one class (the byte-identical twins and their guard test are
gone), and (b) the Deribit-specific wiring: subscribe-on-connect, confirmation filtering, and
the adapter's start/stop lifecycle.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock

import websockets
from algotrading.infra.collectors.ws_listener import WebSocketListener
from algotrading.infra_deribit.collectors.deribit_adapter import DeribitMarketDataAdapter
from algotrading.infra_deribit.connectivity.deribit_transport import DeribitTransport
from algotrading.infra_deribit.connectivity.ws_listener import (
    WebSocketListener as DeribitWebSocketListener,
)
from websockets.sync.server import serve

_DEADLINE_S = 10.0


def test_leaf_import_path_reexports_the_one_shared_listener() -> None:
    """The leaf module is a thin re-export of the canonical infra class — one implementation."""
    assert DeribitWebSocketListener is WebSocketListener


def _wait_until(predicate: Callable[[], bool], timeout: float = _DEADLINE_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@contextmanager
def _ws_server(handler: Callable) -> Iterator[str]:
    with serve(handler, "127.0.0.1", 0) as server:
        host, port = server.socket.getsockname()[:2]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"ws://{host}:{port}"
        finally:
            server.shutdown()


def test_transport_listener_subscribes_and_filters_confirmations() -> None:
    """The listener resends the subscribe message per connection, skips the id-1 confirmation
    frame (no ``method``), and delivers notification frames parsed to dicts."""
    subscriptions: list[dict] = []
    notification = {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {"channel": "ticker.BTC-27JUN25-100000-C.100ms", "data": {"mark_iv": 65.0}},
    }

    def handler(connection) -> None:
        subscriptions.append(json.loads(connection.recv()))
        connection.send(json.dumps({"jsonrpc": "2.0", "id": 1, "result": ["ok"]}))  # confirmation
        connection.send(json.dumps(notification))
        with contextlib.suppress(websockets.exceptions.ConnectionClosed):
            connection.recv()  # hold the connection open until the client stops

    received: list[dict] = []
    with _ws_server(handler) as url:
        transport = DeribitTransport(ws_base=url, client=MagicMock())
        listener = transport.ws_listener(
            ["ticker.BTC-27JUN25-100000-C.100ms"], received.append
        )
        listener.start()
        assert _wait_until(lambda: len(received) >= 1)
        listener.stop()

    assert received == [notification]  # the confirmation frame was filtered out
    assert subscriptions == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "public/subscribe",
            "params": {"channels": ["ticker.BTC-27JUN25-100000-C.100ms"]},
        }
    ]


def test_adapter_subscribe_starts_listener_and_unsubscribe_stops_it() -> None:
    """The adapter owns a real thread lifecycle now — the old code scheduled an asyncio task
    on a loop that was never running, so nothing ever executed."""
    transport = MagicMock()
    transport.get.return_value = {"index_price": 67000.0}
    listener = MagicMock()
    transport.ws_listener.return_value = listener

    adapter = DeribitMarketDataAdapter(transport)
    adapter.subscribe(["OPT:BTC:OPT:20250627:C:100000:1:DERIBIT:USD"])

    channels = transport.ws_listener.call_args.args[0]
    assert channels == ["ticker.BTC-27JUN25-100000-C.100ms"]
    listener.start.assert_called_once()

    adapter.unsubscribe_all()
    listener.stop.assert_called_once()
    assert adapter._subscribed == {}
    assert adapter._index_prices == {}


def test_adapter_ws_fault_reaches_fault_callback() -> None:
    transport = MagicMock()
    transport.get.return_value = {"index_price": 67000.0}
    transport.ws_listener.return_value = MagicMock()

    adapter = DeribitMarketDataAdapter(transport)
    faults = []
    adapter.set_fault_callback(faults.append)
    adapter.subscribe(["OPT:BTC:OPT:20250627:C:100000:1:DERIBIT:USD"])

    on_fault = transport.ws_listener.call_args.kwargs["on_fault"]
    on_fault("connection closed: going away")
    assert len(faults) == 1
    assert faults[0].kind == "other"
    assert "going away" in faults[0].message
