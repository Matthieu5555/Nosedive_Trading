"""Shared WebSocket-listener lifecycle: owned thread, stop event, reconnect with backoff.

One runner replaces the two divergent listener lifecycles the broker leaves used to carry
(a Saxo daemon thread that died on the first recv error, and a Deribit ``create_task`` on a
loop that was never running). The runner owns:

- **the thread** — ``start()`` spawns a daemon thread running its own asyncio loop;
  ``stop()`` signals the stop event and joins promptly, even mid-reconnect-backoff.
- **reconnect** — a dropped connection re-enters the ``websockets`` built-in reconnect
  iterator (``async for ws in connect(...)``, with the library's exponential backoff);
  fatal connect errors are surfaced as faults, then retried after ``restart_backoff_s``
  with a *fresh* connector from ``connect_factory`` (so rotated auth headers apply).
- **the fault seam** — connection losses and fatal errors are reported through ``on_fault``
  instead of silently killing the stream; a bad frame is logged and never ends the session.

This is the single home. It used to live as byte-identical twins in the Saxo and Deribit
leaves (pinned by a twin-guard test) because sibling leaves may not import each other;
both leaves now import it from here. ``websockets`` is imported lazily inside the listen
loop and declared by the broker leaves — importing this module never requires the
streaming stack.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from algotrading.core.log import get_logger

_log = get_logger(__name__)

# Internal pacing invariants (not business parameters): how often the recv loop and the
# stop watcher re-check the stop event, bounding stop() latency to well under a second.
_RECV_POLL_TIMEOUT_S = 1.0
_STOP_POLL_S = 0.1

# Default wait before rebuilding the connector after a *fatal* connect error (transient
# errors use the websockets library's own exponential backoff inside the iterator).
_DEFAULT_RESTART_BACKOFF_S = 5.0


class WebSocketListener:
    """Run one WebSocket subscription on an owned daemon thread, reconnecting until stopped.

    Args:
        connect_factory: returns a fresh ``websockets.connect(...)`` connector. Called once
            per outer (re)start so per-attempt state — e.g. a rotated Bearer token in the
            handshake headers — is re-evaluated; the connector itself then handles transient
            reconnects internally via ``async for``.
        on_frame: synchronous frame handler; receives each raw ``bytes | str`` frame. An
            exception here is logged and the stream continues (one bad frame must not end
            the session).
        on_connect: optional async hook awaited once per established connection, before any
            frame is read — e.g. to (re)send a subscribe message after a reconnect.
        on_fault: optional callback receiving a human-readable reason for each connection
            loss or fatal error; the listener keeps running after reporting.
        name: thread name (also used in log records).
        restart_backoff_s: wait between outer restart attempts after a fatal error.
    """

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the listener thread (idempotent while already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the listener to stop and join its thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    @property
    def is_running(self) -> bool:
        """Whether the listener thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:  # noqa: BLE001 — a listener thread must never propagate into nothing
            _log.exception("WebSocket listener %s terminated with error", self._name)

    async def _main(self) -> None:
        """Race the listen loop against the stop event so stop() interrupts any await."""
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
        # Imported here (not module top) so importing the leaf package never requires the
        # streaming stack — the same convention the REST-only paths rely on.
        import websockets

        while not self._stop_event.is_set():
            try:
                # One connector per outer attempt: transient drops reconnect inside the
                # iterator (library backoff); a fresh factory call re-evaluates auth state.
                async for ws in self._connect_factory():
                    try:
                        if self._on_connect is not None:
                            await self._on_connect(ws)
                        await self._pump(ws)
                    except websockets.exceptions.ConnectionClosed as exc:
                        if self._stop_event.is_set():
                            return
                        self._fault(f"WebSocket connection closed: {exc}; reconnecting")
                        continue  # next iteration of the reconnect iterator
                    if self._stop_event.is_set():
                        return
            except Exception as exc:  # noqa: BLE001 — fatal connect errors are heterogeneous
                if self._stop_event.is_set():
                    return
                self._fault(f"WebSocket listener error: {exc}; restarting after backoff")
                await asyncio.sleep(self._restart_backoff_s)

    async def _pump(self, ws: Any) -> None:
        """Read frames until the connection closes or the stop event is set."""
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_POLL_TIMEOUT_S)
            except TimeoutError:
                continue  # idle poll — re-check the stop event
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
