import threading
import time
from collections.abc import Callable, Mapping

from .cp_rest_transport import SupportsRest

_DEFAULT_KEEPALIVE_S = 60.0
_JOIN_TIMEOUT_S = 5.0

_SSODH_INIT_PATH = "/iserver/auth/ssodh/init"

_REAUTHENTICATE_PATH = "/iserver/reauthenticate"


class SessionNotEstablishedError(Exception):
    pass


def _auth_status_alive(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    status: Mapping[str, object] = payload
    iserver = payload.get("iserver")
    if isinstance(iserver, Mapping) and isinstance(iserver.get("authStatus"), Mapping):
        status = iserver["authStatus"]
    return bool(status.get("authenticated")) and not bool(status.get("competing"))


def _session_established(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    status: Mapping[str, object] = payload
    iserver = payload.get("iserver")
    if isinstance(iserver, Mapping) and isinstance(iserver.get("authStatus"), Mapping):
        status = iserver["authStatus"]
    if not _auth_status_alive(payload):
        return False
    if "established" in status:
        return bool(status.get("established"))
    return bool(status.get("connected"))


class CpRestSession:

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
        return _auth_status_alive(self._transport.get("/iserver/auth/status"))

    def tickle(self) -> bool:
        return _auth_status_alive(self._transport.post("/tickle"))

    def reauthenticate(self) -> None:
        self._transport.post(_REAUTHENTICATE_PATH)

    def open_brokerage_session(self) -> bool:
        return _session_established(self._transport.post(_SSODH_INIT_PATH))

    def established(self) -> bool:
        return _session_established(self._transport.get("/iserver/auth/status"))

    def wait_until_established(
        self, *, max_polls: int, poll_seconds: float
    ) -> None:
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
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._keepalive_loop, name="cp-rest-tickle", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
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
