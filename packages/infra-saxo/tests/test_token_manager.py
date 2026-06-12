"""Tests for TokenManager — all mocked, no network."""

from __future__ import annotations

import time

import pytest
from algotrading.infra_saxo.auth.token_manager import (
    TOKEN_REFRESH_MARGIN_S,
    TokenExpiredError,
    TokenManager,
)


def _make_manager(
    *,
    access_expires_in: int = 1200,
    refresh_expires_in: int = 2400,
    refresh_returns: tuple[str, str, int, int] | None = None,
) -> TokenManager:
    calls: list[str] = []

    def _refresh(refresh_token: str) -> tuple[str, str, int, int]:
        calls.append(refresh_token)
        if refresh_returns is not None:
            return refresh_returns
        return ("new_access", "new_refresh", 1200, 2400)

    mgr = TokenManager(
        refresh_fn=_refresh,
        access_token="init_access",
        refresh_token="init_refresh",
        access_expires_in=access_expires_in,
        refresh_expires_in=refresh_expires_in,
    )
    mgr._calls = calls  # type: ignore[attr-defined]
    return mgr


def test_get_token_returns_initial_token() -> None:
    mgr = _make_manager()
    assert mgr.get_token() == "init_access"


def test_get_token_raises_when_expired() -> None:
    mgr = _make_manager(access_expires_in=0)
    with pytest.raises(TokenExpiredError):
        mgr.get_token()


def test_current_refresh_token_initial() -> None:
    mgr = _make_manager()
    assert mgr.current_refresh_token() == "init_refresh"


def test_do_refresh_updates_state() -> None:
    mgr = _make_manager(refresh_returns=("tok2", "ref2", 600, 1200))
    mgr._do_refresh()
    assert mgr.get_token() == "tok2"
    assert mgr.current_refresh_token() == "ref2"


def test_do_refresh_skipped_when_refresh_token_expired() -> None:
    mgr = _make_manager(refresh_expires_in=0)
    mgr._do_refresh()
    # state should be unchanged — refresh was skipped
    assert mgr.current_refresh_token() == "init_refresh"


def test_do_refresh_handles_exception_without_crash() -> None:
    def _bad_refresh(rt: str) -> tuple[str, str, int, int]:
        raise RuntimeError("network error")

    mgr = TokenManager(
        refresh_fn=_bad_refresh,
        access_token="a",
        refresh_token="r",
    )
    mgr._do_refresh()  # must not raise
    assert mgr.get_token() == "a"


def test_start_stop_thread() -> None:
    mgr = _make_manager(access_expires_in=1200)
    mgr.start()
    assert mgr._thread is not None
    assert mgr._thread.is_alive()
    mgr.stop()
    assert not mgr._thread.is_alive()


def test_refresh_triggered_near_expiry() -> None:
    """Background thread triggers a refresh when access token is almost expired."""
    refreshed: list[bool] = []

    def _refresh(rt: str) -> tuple[str, str, int, int]:
        refreshed.append(True)
        return ("tok2", "ref2", 1200, 2400)

    # Set access_expires_in just below the margin so the loop fires immediately
    mgr = TokenManager(
        refresh_fn=_refresh,
        access_token="a",
        refresh_token="r",
        access_expires_in=TOKEN_REFRESH_MARGIN_S - 5,
    )
    mgr.start()
    deadline = time.monotonic() + 3.0
    while not refreshed and time.monotonic() < deadline:
        time.sleep(0.05)
    mgr.stop()
    assert refreshed, "refresh was not triggered near expiry"
    assert mgr.get_token() == "tok2"


def test_refresh_now_primes_a_pessimistically_seeded_token() -> None:
    """refresh_now forces a refresh; with a 0s seed it yields a valid fresh token."""
    mgr = TokenManager(
        refresh_fn=lambda rt: ("primed_access", "primed_refresh", 1200, 2400),
        access_token="stale",
        refresh_token="r",
        access_expires_in=0,  # pessimistic: treat the seed token as already expired
    )
    assert mgr.refresh_now() is True
    assert mgr.get_token() == "primed_access"


def test_refresh_now_returns_false_when_refresh_fails() -> None:
    """A failed refresh on an already-expired seed leaves no valid token — reported as False."""

    def _bad(rt: str) -> tuple[str, str, int, int]:
        raise RuntimeError("401")

    mgr = TokenManager(
        refresh_fn=_bad, access_token="stale", refresh_token="r", access_expires_in=0
    )
    assert mgr.refresh_now() is False


def test_on_refresh_callback_invoked_with_rotated_tokens() -> None:
    """The on_refresh hook receives the rotated (access, refresh) after a successful refresh."""
    persisted: list[tuple[str, str]] = []

    def _refresh(rt: str) -> tuple[str, str, int, int]:
        return ("tok2", "ref2", 1200, 2400)

    mgr = TokenManager(
        refresh_fn=_refresh,
        access_token="a",
        refresh_token="r",
        on_refresh=lambda access, refresh: persisted.append((access, refresh)),
    )
    mgr._do_refresh()
    assert persisted == [("tok2", "ref2")]


def test_from_token_endpoint_refreshes_via_authlib_form_post() -> None:
    """The live factory speaks the RFC 6749 refresh_token grant (credentials in the body)."""
    import urllib.parse

    import httpx

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["form"] = dict(urllib.parse.parse_qsl(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "AT1",
                "refresh_token": "RT1",
                "expires_in": 1200,
                "refresh_token_expires_in": 2400,
                "token_type": "Bearer",
            },
        )

    rotations: list[tuple[str, str]] = []
    mgr = TokenManager.from_token_endpoint(
        token_url="https://sim.logonvalidation.net/token",
        client_id="CID",
        client_secret="SECRET",
        access_token="stale",
        refresh_token="R0",
        access_expires_in=0,  # pessimistic seed: force the prime to refresh
        on_refresh=lambda a, r: rotations.append((a, r)),
        transport=httpx.MockTransport(handler),
    )
    assert mgr.refresh_now() is True
    assert mgr.get_token() == "AT1"
    assert mgr.current_refresh_token() == "RT1"
    assert rotations == [("AT1", "RT1")]
    assert captured["url"] == "https://sim.logonvalidation.net/token"
    assert captured["form"]["grant_type"] == "refresh_token"
    assert captured["form"]["refresh_token"] == "R0"
    assert captured["form"]["client_id"] == "CID"
    assert captured["form"]["client_secret"] == "SECRET"


def test_on_refresh_failure_does_not_break_refresh() -> None:
    """A persistence failure in on_refresh is swallowed (logged) — the token still updates."""

    def _refresh(rt: str) -> tuple[str, str, int, int]:
        return ("tok2", "ref2", 1200, 2400)

    def _bad_persist(access: str, refresh: str) -> None:
        raise OSError("disk full")

    mgr = TokenManager(
        refresh_fn=_refresh,
        access_token="a",
        refresh_token="r",
        on_refresh=_bad_persist,
    )
    mgr._do_refresh()  # must not raise
    assert mgr.get_token() == "tok2"
