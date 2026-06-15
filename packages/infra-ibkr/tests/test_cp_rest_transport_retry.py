"""CpRestTransport retry behavior: penalty-box backoff, Retry-After, fast-fail, status_code.

These pin the documented retry semantics with independently derived expectations:

* a 429/503 WITHOUT a ``Retry-After`` waits the full penalty box, NOT a sub-second backoff
  (sub-second retries during the documented box are the ban vector this transport exists to
  avoid) — the expected value is the box length, derived from the constructor arg, not the impl;
* a sane numeric ``Retry-After`` header wins over the penalty box, verbatim;
* only 429/503 re-enter the loop — any other status or a connect error fails fast;
* the raised ``CpRestTransportError`` carries the HTTP status as ``status_code`` (``None``
  for a connection-level failure), so callers never reach into ``__cause__``;
* the OAuth signer runs once per attempt (a retry needs a fresh nonce/timestamp).

These tests isolate retry behaviour from the proactive token bucket by DISABLING pacing
(``max_requests_per_second=None``); the bucket has its own dedicated test module. No real
waiting: the injected ``sleep`` records the delays instead of sleeping.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from algotrading.infra_ibkr.connectivity.cp_rest_transport import (
    CpRestTransport,
    CpRestTransportError,
)

_URL = "https://localhost:5000/v1/api/some/path"


def _response(status: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    """A real httpx response bound to a request, so raise_for_status() behaves exactly live."""
    return httpx.Response(
        status,
        headers=headers,
        content=b'{"ok": true}' if status < 400 else b"",
        request=httpx.Request("GET", _URL),
    )


class _ScriptedClient:
    """Returns the scripted responses in order (or raises a scripted exception)."""

    def __init__(self, script: list[httpx.Response | Exception]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def close(self) -> None:
        return None


# Independently chosen box length (not the production default), so the assertions below derive
# their expected wait from THIS value rather than copying the implementation's 600.0.
_BOX_S = 123.0


def _transport(
    client: _ScriptedClient, slept: list[float], *, max_retries: int = 6, **kwargs: Any
) -> CpRestTransport:
    # Pacing OFF here: these tests isolate retry/backoff. penalty_box_s is set unless the caller
    # overrides it, so the expected waits are derived from _BOX_S, not the production default.
    kwargs.setdefault("penalty_box_s", _BOX_S)
    return CpRestTransport(
        _client=client,
        sleep=slept.append,
        max_retries=max_retries,
        max_requests_per_second=None,
        **kwargs,
    )


def test_429_without_retry_after_waits_full_penalty_box_then_succeeds() -> None:
    slept: list[float] = []
    client = _ScriptedClient([_response(429), _response(429), _response(200)])
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    # No Retry-After -> the full penalty box on EVERY retry, never a sub-second backoff.
    assert slept == [_BOX_S, _BOX_S]
    assert len(client.calls) == 3


def test_retry_after_header_wins_over_penalty_box() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(503, headers={"Retry-After": "7"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [7.0]  # the server's wait, verbatim — not the box


def test_http_date_retry_after_falls_back_to_penalty_box() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [_BOX_S]  # unparsable as seconds -> the full penalty box


def test_backoff_base_floors_the_penalty_box() -> None:
    # backoff_base_s is the retained legacy knob; it now floors the no-Retry-After wait. A box
    # smaller than the floor yields the floor, verifying the max(floor, box) contract.
    slept: list[float] = []
    client = _ScriptedClient([_response(429), _response(200)])
    transport = _transport(client, slept, penalty_box_s=1.0, backoff_base_s=9.0)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [9.0]


def test_exhausted_retries_raise_with_status_code() -> None:
    slept: list[float] = []
    # max_retries=2 -> 1 initial attempt + 2 retries = 3 wire calls, then the labeled error.
    client = _ScriptedClient([_response(429)] * 3)
    transport = _transport(client, slept, max_retries=2)

    with pytest.raises(CpRestTransportError) as excinfo:
        transport.get("/some/path")
    assert excinfo.value.status_code == 429
    assert len(client.calls) == 3
    assert slept == [_BOX_S, _BOX_S]  # no sleep after the final failure


def test_non_retryable_status_fails_fast_with_status_code() -> None:
    slept: list[float] = []
    client = _ScriptedClient([_response(400)])
    transport = _transport(client, slept)

    with pytest.raises(CpRestTransportError) as excinfo:
        transport.get("/some/path")
    assert excinfo.value.status_code == 400
    assert slept == []  # not a single backoff
    assert len(client.calls) == 1


def test_connect_error_is_wrapped_without_status_and_not_retried() -> None:
    slept: list[float] = []
    client = _ScriptedClient([httpx.ConnectError("gateway down")])
    transport = _transport(client, slept)

    with pytest.raises(CpRestTransportError) as excinfo:
        transport.get("/some/path")
    assert excinfo.value.status_code is None
    assert slept == []
    assert len(client.calls) == 1


def test_oauth_signer_runs_once_per_attempt() -> None:
    nonces: list[str] = []

    def signer(method: str, url: str, query: Any) -> dict[str, str]:
        nonce = f"nonce-{len(nonces)}"
        nonces.append(nonce)
        return {"Authorization": f'OAuth oauth_nonce="{nonce}"'}

    slept: list[float] = []
    client = _ScriptedClient([_response(503), _response(200)])
    transport = _transport(client, slept, oauth_signer=signer)

    assert transport.get("/some/path") == {"ok": True}
    assert nonces == ["nonce-0", "nonce-1"]  # a fresh signature per attempt, never reused
    assert [c["headers"]["Authorization"] for c in client.calls] == [
        'OAuth oauth_nonce="nonce-0"',
        'OAuth oauth_nonce="nonce-1"',
    ]
