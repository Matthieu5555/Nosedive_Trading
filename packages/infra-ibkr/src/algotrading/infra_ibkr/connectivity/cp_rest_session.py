"""Client Portal brokerage-session lifecycle (ADR 0024).

The TWS path hid the session from us; over REST we own it. The brokerage session times out after
~5–6 minutes of silence, so a long-running collector must ``POST /tickle`` roughly every minute or
it silently dies. A dropped/expired session is surfaced via the ``on_drop`` callback — the
disconnect signal the engine's backoff owns. No secrets, nothing persisted: the local CP Gateway
holds the session.

Threading mirrors infra-saxo's ``TokenManager`` (daemon thread, ``threading.Event`` stop, injected
sleep for deterministic tests). The decision pieces (:meth:`authenticated`, :meth:`tickle`) are
pure-ish wrappers over the transport and are unit-tested directly.
"""

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

_DEFAULT_KEEPALIVE_S = 60.0
_JOIN_TIMEOUT_S = 5.0


class _SupportsRest(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...
    def post(self, path: str, body: dict[str, Any] | None = None) -> Any: ...


def _auth_status_alive(payload: object) -> bool:
    """True when an auth-status / tickle payload reports a live, non-competing session."""
    if not isinstance(payload, Mapping):
        return False
    # /tickle nests it under iserver.authStatus; /iserver/auth/status returns it flat.
    status: Mapping[str, object] = payload
    iserver = payload.get("iserver")
    if isinstance(iserver, Mapping) and isinstance(iserver.get("authStatus"), Mapping):
        status = iserver["authStatus"]
    return bool(status.get("authenticated")) and not bool(status.get("competing"))


class CpRestSession:
    """Auth-status check + ``/tickle`` keepalive for the Client Portal brokerage session."""

    def __init__(
        self,
        transport: _SupportsRest,
        *,
        keepalive_seconds: float = _DEFAULT_KEEPALIVE_S,
        on_drop: Callable[[], None] | None = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._keepalive_seconds = keepalive_seconds
        self._on_drop = on_drop
        self._sleep = _sleep
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def authenticated(self) -> bool:
        """GET ``/iserver/auth/status`` → whether the brokerage session is live."""
        return _auth_status_alive(self._transport.get("/iserver/auth/status"))

    def tickle(self) -> bool:
        """POST ``/tickle`` to keep the session alive; return whether it is still live."""
        return _auth_status_alive(self._transport.post("/tickle"))

    def start(self) -> None:
        """Spawn the daemon keepalive thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._keepalive_loop, name="cp-rest-tickle", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the keepalive thread to stop and join it.

        Safe to call from the keepalive thread itself (e.g. inside ``on_drop``): joining the
        current thread is skipped — setting the stop event is enough to end the loop.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_JOIN_TIMEOUT_S)
            self._thread = None

    def _keepalive_loop(self) -> None:
        while not self._stop_event.is_set():
            self._sleep(self._keepalive_seconds)
            if self._stop_event.is_set():
                break
            if not self.tickle():
                if self._on_drop is not None:
                    self._on_drop()
                break
