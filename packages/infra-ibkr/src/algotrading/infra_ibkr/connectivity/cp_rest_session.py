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

from .cp_rest_transport import SupportsRest

_DEFAULT_KEEPALIVE_S = 60.0
_JOIN_TIMEOUT_S = 5.0

# Brokerage-session open endpoint (ADR 0031 §3): POST it, then poll status until the
# session reports `established: true` before any history request.
_SSODH_INIT_PATH = "/iserver/auth/ssodh/init"

# Brokerage-session revive endpoint: when the SSO cookie is still valid but the brokerage
# session lapsed (`authenticated: true, connected: false`), POSTing this re-opens it without
# a fresh interactive login (no new SMS).
_REAUTHENTICATE_PATH = "/iserver/reauthenticate"


class SessionNotEstablishedError(Exception):
    """The brokerage session never reported ``established: true`` within the wait budget.

    A labeled error (never a silent proceed): a history request fired into a not-yet-
    established session is exactly the failure the established-wait guards against.
    """


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


def _session_established(payload: object) -> bool:
    """True when a status/ssodh payload reports the brokerage session established.

    ``ssodh/init`` and ``/iserver/auth/status`` both carry the brokerage-session readiness
    under ``connected`` + ``authenticated``; the explicit ``established`` flag (when present)
    must also be true. Reads the same nested/flat shapes :func:`_auth_status_alive` does.
    """
    if not isinstance(payload, Mapping):
        return False
    status: Mapping[str, object] = payload
    iserver = payload.get("iserver")
    if isinstance(iserver, Mapping) and isinstance(iserver.get("authStatus"), Mapping):
        status = iserver["authStatus"]
    if not _auth_status_alive(payload):
        return False
    # `established` is the authoritative readiness flag when the payload carries it; absent,
    # an authenticated+connected session is treated as established (the status endpoint shape).
    if "established" in status:
        return bool(status.get("established"))
    return bool(status.get("connected"))


class CpRestSession:
    """Auth-status check + ``/tickle`` keepalive for the Client Portal brokerage session."""

    def __init__(
        self,
        transport: SupportsRest,
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

    def reauthenticate(self) -> None:
        """POST ``/iserver/reauthenticate`` to revive a lapsed brokerage session — no new login.

        The Gateway's SSO cookie outlives the brokerage session: when the status reads
        authenticated-but-disconnected, this re-opens the brokerage side without a fresh
        interactive login (no SMS). Fire-and-forget: the response carries only a trigger
        message, so callers re-check liveness via :meth:`authenticated` / :meth:`established`
        after a short grace. A fully-expired SSO cookie is *not* revivable this way — that
        needs a fresh browser login.
        """
        self._transport.post(_REAUTHENTICATE_PATH)

    def open_brokerage_session(self) -> bool:
        """POST ``ssodh/init`` to open the brokerage session; return whether it is established.

        Idempotent on IBKR's side — re-initialising an already-open session is harmless. The
        returned bool is whether the immediate response already reports the session
        established; a not-yet-established response is normal and the caller polls via
        :meth:`wait_until_established`.
        """
        return _session_established(self._transport.post(_SSODH_INIT_PATH))

    def established(self) -> bool:
        """GET ``/iserver/auth/status`` → whether the brokerage session is established.

        Distinct from :meth:`authenticated`: a session can be authenticated yet not yet
        established (the brokerage handshake still completing). History requires established.
        """
        return _session_established(self._transport.get("/iserver/auth/status"))

    def wait_until_established(
        self, *, max_polls: int, poll_seconds: float
    ) -> None:
        """Open the brokerage session and block until it is established (injected sleep).

        Posts ``ssodh/init`` once, then polls ``/iserver/auth/status`` up to ``max_polls``
        times, sleeping ``poll_seconds`` between polls via the injected ``_sleep`` (no real
        wait in tests). Returns as soon as the session is established; raises a labeled
        :class:`SessionNotEstablishedError` if it never establishes within the budget —
        never silently proceeds to fire a history request into a dead session.
        """
        if self.open_brokerage_session():
            return
        for _poll in range(max_polls):
            self._sleep(poll_seconds)
            if self._stop_event.is_set():
                raise SessionNotEstablishedError("stopped while waiting for established session")
            if self.established():
                return
        raise SessionNotEstablishedError(
            f"brokerage session not established after {max_polls} polls"
        )

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
