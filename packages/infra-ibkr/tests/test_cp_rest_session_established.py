"""Brokerage-session establishment gate for the history path (ADR 0031, Part C).

No live Gateway and no real sleep: a fake transport returns canned ssodh/status payloads and the
established-wait is driven with an injected sleep, as ``test_cp_rest_session.py`` does for the
tickler. The named obligations: ``ssodh/init`` is posted, the wait blocks until
``established: true``, and a never-establishing session raises a labeled error (never a silent
proceed into a request against a dead session).
"""

from __future__ import annotations

from typing import Any

import pytest
from algotrading.infra_ibkr.connectivity.cp_rest_session import (
    CpRestSession,
    SessionNotEstablishedError,
)

_NOT_ESTABLISHED = {"authenticated": True, "competing": False, "connected": True, "established": False}
_ESTABLISHED = {"authenticated": True, "competing": False, "connected": True, "established": True}


class _FakeTransport:
    def __init__(self, *, init: Any, status_sequence: list[Any] | None = None) -> None:
        self._init = init
        self._status_sequence = list(status_sequence or [])
        self.posts: list[str] = []
        self.gets: list[str] = []

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        self.posts.append(path)
        return self._init

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.gets.append(path)
        return self._status_sequence.pop(0) if self._status_sequence else _NOT_ESTABLISHED


def test_open_brokerage_session_posts_ssodh_init() -> None:
    transport = _FakeTransport(init=_ESTABLISHED)
    session = CpRestSession(transport)
    assert session.open_brokerage_session() is True
    assert transport.posts == ["/iserver/auth/ssodh/init"]


def test_wait_returns_immediately_when_init_already_established() -> None:
    transport = _FakeTransport(init=_ESTABLISHED)
    slept: list[float] = []
    session = CpRestSession(transport, _sleep=slept.append)
    session.wait_until_established(max_polls=5, poll_seconds=1.0)
    # Established on init: no polling, no sleeping.
    assert transport.gets == []
    assert slept == []


def test_wait_polls_until_established() -> None:
    # init returns not-established; status polls twice not-established, then established.
    transport = _FakeTransport(
        init=_NOT_ESTABLISHED,
        status_sequence=[_NOT_ESTABLISHED, _ESTABLISHED],
    )
    slept: list[float] = []
    session = CpRestSession(transport, _sleep=slept.append)
    session.wait_until_established(max_polls=5, poll_seconds=0.5)
    assert transport.posts == ["/iserver/auth/ssodh/init"]
    assert transport.gets == ["/iserver/auth/status", "/iserver/auth/status"]
    assert slept == [0.5, 0.5]  # slept before each poll, no real wait


def test_never_established_raises_labeled_error() -> None:
    transport = _FakeTransport(
        init=_NOT_ESTABLISHED,
        status_sequence=[_NOT_ESTABLISHED] * 10,
    )
    session = CpRestSession(transport, _sleep=lambda _s: None)
    with pytest.raises(SessionNotEstablishedError, match="not established after 3 polls"):
        session.wait_until_established(max_polls=3, poll_seconds=0.1)


def test_authenticated_but_not_established_is_not_established() -> None:
    # A session can be authenticated yet not established; established() must report False.
    transport = _FakeTransport(init=_NOT_ESTABLISHED, status_sequence=[_NOT_ESTABLISHED])
    session = CpRestSession(transport)
    assert session.established() is False
