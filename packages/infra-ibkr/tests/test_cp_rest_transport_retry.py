"""CpRestTransport retry behavior: cadence, Retry-After, fast-fail, and status_code.

These pin the documented retry semantics across the tenacity adoption (audit M20) with
independently derived expectations:

* the delay schedule is ``backoff_base * 2**retry_index`` (0-based), hand-computed below;
* a sane numeric ``Retry-After`` header wins over the computed backoff, verbatim;
* only 429/503 re-enter the loop — any other status or a connect error fails fast;
* the raised ``CpRestTransportError`` carries the HTTP status as ``status_code`` (``None``
  for a connection-level failure), so callers never reach into ``__cause__``;
* the OAuth signer runs once per attempt (a retry needs a fresh nonce/timestamp).

No real waiting: the injected ``sleep`` records the delays instead of sleeping.
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


def _transport(
    client: _ScriptedClient, slept: list[float], *, max_retries: int = 6, **kwargs: Any
) -> CpRestTransport:
    return CpRestTransport(
        _client=client, sleep=slept.append, max_retries=max_retries, **kwargs
    )


def test_429_retries_with_doubling_backoff_then_succeeds() -> None:
    slept: list[float] = []
    client = _ScriptedClient([_response(429), _response(429), _response(200)])
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    # Hand-derived: default base 0.5s, doubling per retry index -> 0.5 * 2**0, 0.5 * 2**1.
    assert slept == [0.5, 1.0]
    assert len(client.calls) == 3


def test_retry_after_header_wins_over_computed_backoff() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(503, headers={"Retry-After": "7"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [7.0]  # the server's wait, verbatim — not 0.5


def test_http_date_retry_after_falls_back_to_backoff() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [0.5]  # unparsable as seconds -> our own first-retry backoff


def test_exhausted_retries_raise_with_status_code() -> None:
    slept: list[float] = []
    # max_retries=2 -> 1 initial attempt + 2 retries = 3 wire calls, then the labeled error.
    client = _ScriptedClient([_response(429)] * 3)
    transport = _transport(client, slept, max_retries=2)

    with pytest.raises(CpRestTransportError) as excinfo:
        transport.get("/some/path")
    assert excinfo.value.status_code == 429
    assert len(client.calls) == 3
    assert slept == [0.5, 1.0]  # no sleep after the final failure


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
