from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import websockets
from algotrading.infra.collectors.ws_listener import WebSocketListener
from websockets.sync.server import ServerConnection, serve

_DEADLINE_S = 10.0


def _wait_until(predicate: Callable[[], bool], timeout: float = _DEADLINE_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@contextmanager
def _ws_server(handler: Callable[[ServerConnection], None]) -> Iterator[str]:
    with serve(handler, "127.0.0.1", 0) as server:
        host, port = server.socket.getsockname()[:2]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"ws://{host}:{port}"
        finally:
            server.shutdown()


def _hold_open(connection: ServerConnection) -> None:
    with contextlib.suppress(websockets.exceptions.ConnectionClosed):
        connection.recv()


def test_frames_flow_and_on_connect_runs_first() -> None:
    received_subscribes: list[bytes | str] = []
    frames: list[bytes | str] = []

    def handler(connection: ServerConnection) -> None:
        received_subscribes.append(connection.recv())
        connection.send("frame-a")
        connection.send("frame-b")
        _hold_open(connection)

    async def on_connect(ws: websockets.ClientConnection) -> None:
        await ws.send("SUBSCRIBE")

    with _ws_server(handler) as url:
        listener = WebSocketListener(
            connect_factory=lambda: websockets.connect(url),
            on_frame=frames.append,
            on_connect=on_connect,
        )
        listener.start()
        assert _wait_until(lambda: len(frames) >= 2)
        listener.stop()

    assert frames[:2] == ["frame-a", "frame-b"]
    assert received_subscribes == ["SUBSCRIBE"]
    assert not listener.is_running


def test_reconnects_after_connection_drop_and_resends_subscribe() -> None:
    connections = 0
    frames: list[bytes | str] = []
    faults: list[str] = []
    lock = threading.Lock()

    def handler(connection: ServerConnection) -> None:
        nonlocal connections
        with lock:
            connections += 1
            n = connections
        connection.recv()
        connection.send(f"frame-{n}")
        if n == 1:
            return
        _hold_open(connection)

    async def on_connect(ws: websockets.ClientConnection) -> None:
        await ws.send("SUBSCRIBE")

    with _ws_server(handler) as url:
        listener = WebSocketListener(
            connect_factory=lambda: websockets.connect(url),
            on_frame=frames.append,
            on_connect=on_connect,
            on_fault=faults.append,
        )
        listener.start()
        assert _wait_until(lambda: "frame-2" in frames)
        listener.stop()

    assert frames[:2] == ["frame-1", "frame-2"]
    assert connections >= 2
    assert any("closed" in fault for fault in faults)


def test_bad_frame_does_not_end_the_session() -> None:
    frames: list[bytes | str] = []

    def handler(connection: ServerConnection) -> None:
        connection.send("bad")
        connection.send("good")
        _hold_open(connection)

    def on_frame(raw: bytes | str) -> None:
        if raw == "bad":
            raise ValueError("malformed frame")
        frames.append(str(raw))

    with _ws_server(handler) as url:
        listener = WebSocketListener(
            connect_factory=lambda: websockets.connect(url),
            on_frame=on_frame,
        )
        listener.start()
        assert _wait_until(lambda: "good" in frames)
        listener.stop()


def test_fatal_factory_error_is_reported_and_recovered() -> None:
    attempts = 0
    frames: list[bytes | str] = []
    faults: list[str] = []

    def handler(connection: ServerConnection) -> None:
        connection.send("after-recovery")
        _hold_open(connection)

    with _ws_server(handler) as url:

        def factory() -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("token endpoint down")
            return websockets.connect(url)

        listener = WebSocketListener(
            connect_factory=factory,
            on_frame=frames.append,
            on_fault=faults.append,
            restart_backoff_s=0.05,
        )
        listener.start()
        assert _wait_until(lambda: "after-recovery" in frames)
        listener.stop()

    assert attempts >= 2
    assert any("token endpoint down" in fault for fault in faults)


def test_stop_joins_thread_promptly() -> None:
    def handler(connection: ServerConnection) -> None:
        _hold_open(connection)

    with _ws_server(handler) as url:
        listener = WebSocketListener(
            connect_factory=lambda: websockets.connect(url),
            on_frame=lambda raw: None,
        )
        listener.start()
        assert _wait_until(lambda: listener.is_running)
        listener.stop(timeout=5.0)
        assert not listener.is_running


def test_start_is_idempotent_while_running() -> None:
    def handler(connection: ServerConnection) -> None:
        _hold_open(connection)

    with _ws_server(handler) as url:
        listener = WebSocketListener(
            connect_factory=lambda: websockets.connect(url),
            on_frame=lambda raw: None,
        )
        listener.start()
        thread_first = listener._thread
        listener.start()
        assert listener._thread is thread_first
        listener.stop()
