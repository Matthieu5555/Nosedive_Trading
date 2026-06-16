from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from algotrading.core.log import get_logger

_log = get_logger(__name__)

_RECV_POLL_TIMEOUT_S = 1.0
_STOP_POLL_S = 0.1

_DEFAULT_RESTART_BACKOFF_S = 5.0


class WebSocketListener:

    def __init__(
        self,
        *,
        connect_factory: Callable[[], Any],
        on_frame: Callable[[bytes | str], None],
        on_connect: Callable[[Any], Awaitable[None]] | None = None,
        on_fault: Callable[[str], None] | None = None,
        name: str = "ws-listener",
        restart_backoff_s: float = _DEFAULT_RESTART_BACKOFF_S,
    ) -> None:
        self._connect_factory = connect_factory
        self._on_frame = on_frame
        self._on_connect = on_connect
        self._on_fault = on_fault
        self._name = name
        self._restart_backoff_s = restart_backoff_s
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None


    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:  # noqa: BLE001 — a listener thread must never propagate into nothing
            _log.exception("WebSocket listener %s terminated with error", self._name)

    async def _main(self) -> None:
        listen = asyncio.ensure_future(self._listen_forever())
        stop = asyncio.ensure_future(self._await_stop())
        done, pending = await asyncio.wait({listen, stop}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc

    async def _await_stop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(_STOP_POLL_S)

    async def _listen_forever(self) -> None:
        import websockets

        while not self._stop_event.is_set():
            try:
                async for ws in self._connect_factory():
                    try:
                        if self._on_connect is not None:
                            await self._on_connect(ws)
                        await self._pump(ws)
                    except websockets.exceptions.ConnectionClosed as exc:
                        if self._stop_event.is_set():
                            return
                        self._fault(f"WebSocket connection closed: {exc}; reconnecting")
                        continue
                    if self._stop_event.is_set():
                        return
            except Exception as exc:  # noqa: BLE001 — fatal connect errors are heterogeneous
                if self._stop_event.is_set():
                    return
                self._fault(f"WebSocket listener error: {exc}; restarting after backoff")
                await asyncio.sleep(self._restart_backoff_s)

    async def _pump(self, ws: Any) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_POLL_TIMEOUT_S)
            except TimeoutError:
                continue
            try:
                self._on_frame(raw)
            except Exception:  # noqa: BLE001 — one bad frame must not end the session
                _log.exception("WebSocket frame handler failed in %s", self._name)

    def _fault(self, reason: str) -> None:
        _log.warning("%s: %s", self._name, reason)
        if self._on_fault is not None:
            try:
                self._on_fault(reason)
            except Exception:  # noqa: BLE001 — fault reporting is best-effort
                _log.exception("WebSocket fault callback failed in %s", self._name)
