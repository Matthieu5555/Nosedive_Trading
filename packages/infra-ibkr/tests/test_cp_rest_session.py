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
    assert CpRestSession(FakeCpTransport(get_response=_COMPETING)).authenticated() is False


def test_reauthenticate_posts_the_revive_endpoint() -> None:
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
        _sleep=lambda _seconds: None,
    )
    session._keepalive_loop()
    assert transport.post_paths == ["/tickle", "/tickle"]
    assert dropped == [True]


def test_start_stop_is_clean() -> None:
    holder: dict[str, CpRestSession] = {}

    def _sleep(_seconds: float) -> None:
        holder["session"].stop()

    session = CpRestSession(FakeCpTransport(post_queue=[_TICKLE_ALIVE] * 100), _sleep=_sleep)
    holder["session"] = session
    session.start()
    session.stop()
