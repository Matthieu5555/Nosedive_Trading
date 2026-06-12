"""Client Portal brokerage-session lifecycle (ADR 0024) — auth status + /tickle keepalive.

No live Gateway: a fake transport returns canned auth payloads, and the keepalive loop is driven
synchronously with a no-op sleep so a dropped session deterministically triggers ``on_drop``.
"""

from __future__ import annotations

from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession

from .conftest import FakeCpTransport

_ALIVE = {"authenticated": True, "competing": False, "connected": True}
_DROPPED = {"authenticated": False, "competing": False, "connected": False}
_COMPETING = {"authenticated": True, "competing": True, "connected": True}
_TICKLE_ALIVE = {"iserver": {"authStatus": _ALIVE}}
_TICKLE_DROPPED = {"iserver": {"authStatus": _DROPPED}}


def test_authenticated_parses_flat_status() -> None:
    assert CpRestSession(FakeCpTransport(get_response=_ALIVE)).authenticated() is True
    assert CpRestSession(FakeCpTransport(get_response=_DROPPED)).authenticated() is False
    # A competing session (another login took over) is not a usable session.
    assert CpRestSession(FakeCpTransport(get_response=_COMPETING)).authenticated() is False


def test_reauthenticate_posts_the_revive_endpoint() -> None:
    # The self-heal seam the keepalive scripts ride: a lapsed brokerage session (SSO cookie still
    # valid) is revived by POSTing /iserver/reauthenticate — fire-and-forget, no return contract.
    transport = FakeCpTransport(post_queue=[{"message": "triggered"}])
    CpRestSession(transport).reauthenticate()
    assert transport.post_paths == ["/iserver/reauthenticate"]


def test_tickle_parses_nested_status() -> None:
    alive = CpRestSession(FakeCpTransport(post_queue=[_TICKLE_ALIVE]))
    assert alive.tickle() is True
    dropped = CpRestSession(FakeCpTransport(post_queue=[_TICKLE_DROPPED]))
    assert dropped.tickle() is False


def test_keepalive_fires_then_surfaces_a_drop() -> None:
    dropped: list[bool] = []
    transport = FakeCpTransport(post_queue=[_TICKLE_ALIVE, _TICKLE_DROPPED])
    session = CpRestSession(
        transport,
        on_drop=lambda: dropped.append(True),
        _sleep=lambda _seconds: None,  # no real waiting
    )
    # Drive the loop body directly (no thread): it tickles until one reports the session dropped.
    session._keepalive_loop()
    assert transport.post_paths == ["/tickle", "/tickle"]  # kept alive once, then re-checked
    assert dropped == [True]  # the drop was surfaced exactly once


def test_start_stop_is_clean() -> None:
    # A live-alive tickle would loop forever; stop() must break it. A sleep that stops the session
    # on first call lets the daemon thread exit promptly.
    holder: dict[str, CpRestSession] = {}

    def _sleep(_seconds: float) -> None:
        holder["session"].stop()

    session = CpRestSession(FakeCpTransport(post_queue=[_TICKLE_ALIVE] * 100), _sleep=_sleep)
    holder["session"] = session
    session.start()
    session.stop()  # idempotent join; thread already exiting
