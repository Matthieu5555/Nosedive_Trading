"""Saxo Bank OAuth2 token lifecycle: automatic refresh, rotation, and expiry guard.

Access tokens expire after 20 minutes (expires_in=1200). Refresh tokens expire after
40 minutes (expires_in=2400) and are rotated on every use — each refresh call returns a
new refresh token that invalidates the previous one. A background thread triggers a new
refresh when the access token has less than TOKEN_REFRESH_MARGIN_S seconds remaining,
providing a safety window for the network round-trip. If the window is missed entirely,
TokenExpiredError is raised on the next get_token() call.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from algotrading.core.log import get_logger

_log = get_logger(__name__)

TOKEN_REFRESH_MARGIN_S: int = 120


class TokenExpiredError(Exception):
    """Raised when both the access and refresh tokens have expired."""


@dataclass
class _TokenState:
    access_token: str
    refresh_token: str
    access_expires_at: float  # monotonic
    refresh_expires_at: float  # monotonic


class TokenManager:
    """Thread-safe OAuth2 token manager for the Saxo Bank OpenAPI.

    Call ``start()`` after construction to launch the background refresh thread.
    Call ``stop()`` to shut it down cleanly. Use ``get_token()`` to obtain a valid
    Bearer token for use in HTTP headers.

    ``refresh_fn`` is injected for testability — in production it delegates to the
    OAuth2 token endpoint via httpx.
    """

    def __init__(
        self,
        *,
        refresh_fn: Callable[[str], tuple[str, str, int, int]],
        access_token: str,
        refresh_token: str,
        access_expires_in: int = 1200,
        refresh_expires_in: int = 2400,
        on_refresh: Callable[[str, str], None] | None = None,
    ) -> None:
        """
        Args:
            refresh_fn: ``(refresh_token) -> (access_token, refresh_token, access_expires_in,
                refresh_expires_in)`` — calls the OAuth2 /token endpoint.
            access_token: the access token obtained from the initial auth flow.
            refresh_token: the refresh token obtained from the initial auth flow.
            access_expires_in: lifetime of the initial access token in seconds.
            refresh_expires_in: lifetime of the initial refresh token in seconds.
            on_refresh: optional ``(access_token, refresh_token) -> None`` hook invoked after each
                successful refresh, e.g. to persist the rotated tokens. Its failures are logged and
                never interrupt the refresh loop.
        """
        self._refresh_fn = refresh_fn
        self._on_refresh = on_refresh
        now = time.monotonic()
        self._state = _TokenState(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=now + access_expires_in,
            refresh_expires_at=now + refresh_expires_in,
        )
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background refresh thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._refresh_loop, daemon=True, name="saxo-token-refresh"
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def get_token(self) -> str:
        """Return a valid Bearer access token, or raise TokenExpiredError if expired."""
        with self._lock:
            state = self._state
            if time.monotonic() >= state.access_expires_at:
                raise TokenExpiredError(
                    "Saxo access token has expired and was not refreshed in time"
                )
            return state.access_token

    def refresh_now(self) -> bool:
        """Force one synchronous refresh now and report whether a valid token results.

        Used to prime a session: the access token loaded from storage may already be stale, so a
        caller seeds the manager pessimistically (access_expires_in=0) and primes with this before
        the first request. Returns False when the refresh failed and no valid token is available.
        """
        self._do_refresh()
        try:
            self.get_token()
        except TokenExpiredError:
            return False
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                state = self._state
                time_until_expiry = state.access_expires_at - time.monotonic()

            if time_until_expiry <= TOKEN_REFRESH_MARGIN_S:
                self._do_refresh()
                sleep_s = max(1.0, TOKEN_REFRESH_MARGIN_S - 10)
            else:
                sleep_s = max(1.0, time_until_expiry - TOKEN_REFRESH_MARGIN_S)

            self._stop_event.wait(timeout=sleep_s)

    def _do_refresh(self) -> None:
        with self._lock:
            refresh_token = self._state.refresh_token
            if time.monotonic() >= self._state.refresh_expires_at:
                _log.error("Saxo refresh token has expired — cannot obtain a new access token")
                return

        try:
            access_token, new_refresh_token, access_expires_in, refresh_expires_in = (
                self._refresh_fn(refresh_token)
            )
        except Exception:  # noqa: BLE001 — refresh_fn surfaces heterogeneous vendor/network errors
            _log.exception("Saxo token refresh failed")
            return

        now = time.monotonic()
        with self._lock:
            self._state = _TokenState(
                access_token=access_token,
                refresh_token=new_refresh_token,
                access_expires_at=now + access_expires_in,
                refresh_expires_at=now + refresh_expires_in,
            )
        _log.info(
            "Saxo token refreshed — next expiry in %ds",
            access_expires_in,
        )
        if self._on_refresh is not None:
            # Persist the rotated tokens outside the lock; a persistence failure must not break
            # the refresh loop (the in-memory token is already valid for this session).
            try:
                self._on_refresh(access_token, new_refresh_token)
            except Exception:  # noqa: BLE001 — persistence is best-effort; log and keep refreshing
                _log.exception("Saxo token persistence (on_refresh) failed")

    def get_access_expires_wall_clock(self) -> float:
        """Wall-clock Unix timestamp at which the current access token expires."""
        with self._lock:
            monotonic_delta = self._state.access_expires_at - time.monotonic()
            return time.time() + monotonic_delta

    # Convenience: expose the refresh token for persistence (e.g. writing to disk)
    def current_refresh_token(self) -> str:
        """Return the current refresh token for external persistence."""
        with self._lock:
            return self._state.refresh_token

    @staticmethod
    def from_token_endpoint(
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        access_expires_in: int = 1200,
        refresh_expires_in: int = 2400,
        on_refresh: Callable[[str, str], None] | None = None,
    ) -> TokenManager:
        """Factory that wires the refresh_fn to the live Saxo token endpoint via httpx."""
        import httpx  # local import — only needed in the live path

        def _refresh(refresh_token: str) -> tuple[str, str, int, int]:
            resp = httpx.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            body = resp.json()
            return (
                body["access_token"],
                body["refresh_token"],
                int(body.get("expires_in", 1200)),
                int(body.get("refresh_token_expires_in", 2400)),
            )

        return TokenManager(
            refresh_fn=_refresh,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_in=access_expires_in,
            refresh_expires_in=refresh_expires_in,
            on_refresh=on_refresh,
        )
