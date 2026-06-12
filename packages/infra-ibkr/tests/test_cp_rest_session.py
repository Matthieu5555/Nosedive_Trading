"""Client Portal brokerage-session lifecycle (ADR 0024) — auth status + /tickle keepalive.

No live Gateway: a fake transport returns canned auth payloads, and the keepalive loop is driven
synchronously with a no-op sleep so a dropped session deterministically triggers ``on_drop``.
"""

from __future__ import annotations

from typing import Any

from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession

_ALIVE = {"authenticated": True, "competing": False, "connected": True}
_DROPPED = {"authenticated": False, "competing": False, "connected": False}
_COMPETING = {"authenticated": True, "competing": True, "connected": True}
_TICKLE_ALIVE = {"iserver": {"authStatus": _ALIVE}}
_TICKLE_DROPPED = {"iserver": {"authStatus": _DROPPED}}


class _FakeTransport:
    def __init__(self, *, status: Any = None, tickle_sequence: list[Any] | None = None) -> None:
        self._status = status
        self._tickle_sequence = list(tickle_sequence or [])
        self.posts: list[str] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._status

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        self.posts.append(path)
        return self._tickle_sequence.pop(0)


def test_authenticated_parses_flat_status() -> None:
    assert CpRestSession(_FakeTransport(status=_ALIVE)).authenticated() is True
    assert CpRestSession(_FakeTransport(status=_DROPPED)).authenticated() is False
    # A competing session (another login took over) is not a usable session.
    assert CpRestSession(_FakeTransport(status=_COMPETING)).authenticated() is False


def test_reauthenticate_posts_the_revive_endpoint() -> None:
    # The self-heal seam the keepalive scripts ride: a lapsed brokerage session (SSO cookie still
    # valid) is revived by POSTing /iserver/reauthenticate — fire-and-forget, no return contract.
    transport = _FakeTransport(tickle_sequence=[{"message": "triggered"}])
    CpRestSession(transport).reauthenticate()
    assert transport.posts == ["/iserver/reauthenticate"]


def test_tickle_parses_nested_status() -> None:
    alive = CpRestSession(_FakeTransport(tickle_sequence=[_TICKLE_ALIVE]))
    assert alive.tickle() is True
    dropped = CpRestSession(_FakeTransport(tickle_sequence=[_TICKLE_DROPPED]))
    assert dropped.tickle() is False


def test_keepalive_fires_then_surfaces_a_drop() -> None:
    dropped: list[bool] = []
    transport = _FakeTransport(tickle_sequence=[_TICKLE_ALIVE, _TICKLE_DROPPED])
    session = CpRestSession(
        transport,
        on_drop=lambda: dropped.append(True),
        _sleep=lambda _seconds: None,  # no real waiting
    )
    # Drive the loop body directly (no thread): it tickles until one reports the session dropped.
    session._keepalive_loop()
    assert transport.posts == ["/tickle", "/tickle"]  # kept alive once, then re-checked
    assert dropped == [True]  # the drop was surfaced exactly once


def test_start_stop_is_clean() -> None:
    # A live-alive tickle would loop forever; stop() must break it. A sleep that stops the session
    # on first call lets the daemon thread exit promptly.
    holder: dict[str, CpRestSession] = {}

    def _sleep(_seconds: float) -> None:
        holder["session"].stop()

    session = CpRestSession(_FakeTransport(tickle_sequence=[_TICKLE_ALIVE] * 100), _sleep=_sleep)
    holder["session"] = session
    session.start()
    session.stop()  # idempotent join; thread already exiting
