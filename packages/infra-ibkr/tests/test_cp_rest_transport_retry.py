"""CpRestTransport retry behavior: bounded backoff, Retry-After, fast-fail, status_code.

These pin the documented retry semantics with independently derived expectations:

* a 429/503 WITHOUT a ``Retry-After`` backs off a BOUNDED exponential — ``backoff_base * 2**(n)``
  capped at the no-Retry-After cap — NOT the full penalty box (the old full-box wait froze a
  pooled worker for ~10 minutes on the first routine burst). The expected values are derived from
  ``backoff_base_s`` and the attempt index, not copied from the impl;
* the bounded backoff is CAPPED, so a deep retry sequence can never balloon into a multi-minute
  wait;
* a sane numeric ``Retry-After`` header is honoured verbatim, but bounded by ``penalty_box_s`` so
  a pathological header cannot wedge the walk — that bound is the only path that may wait minutes,
  and only because the server asked;
* an unparsable (HTTP-date) ``Retry-After`` falls back to the bounded backoff, not the box;
* only 429/503 re-enter the loop — any other status or a connect error fails fast;
* the raised ``CpRestTransportError`` carries the HTTP status as ``status_code`` (``None``
  for a connection-level failure), so callers never reach into ``__cause__``;
* the OAuth signer runs once per attempt (a retry needs a fresh nonce/timestamp).

These tests isolate retry behaviour from the proactive token bucket by DISABLING pacing
(``max_requests_per_second=None``) and zeroing jitter; the bucket has its own dedicated test
module. No real waiting: the injected ``sleep`` records the delays instead of sleeping.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from algotrading.infra_ibkr.connectivity.cp_rest_transport import (
    _NO_RETRY_AFTER_BACKOFF_CAP_S,
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


# Independently chosen box bound (not the production default), so the Retry-After-bound assertions
# below derive their expected wait from THIS value rather than copying the implementation's 600.0.
_BOX_S = 123.0
# The no-Retry-After backoff base used in these tests (not the production default), so expected
# waits are derived as ``_BASE_S * 2**n``, not copied from the impl.
_BASE_S = 0.5


def _transport(
    client: _ScriptedClient, slept: list[float], *, max_retries: int = 6, **kwargs: Any
) -> CpRestTransport:
    # Pacing OFF and jitter ZEROED here: these tests isolate retry/backoff math. penalty_box_s and
    # backoff_base_s are set unless overridden, so expected waits derive from _BOX_S / _BASE_S, not
    # the production defaults.
    kwargs.setdefault("penalty_box_s", _BOX_S)
    kwargs.setdefault("backoff_base_s", _BASE_S)
    kwargs.setdefault("jitter", lambda: 0.0)
    return CpRestTransport(
        _client=client,
        sleep=slept.append,
        max_retries=max_retries,
        max_requests_per_second=None,
        **kwargs,
    )


def test_429_without_retry_after_uses_bounded_exponential_backoff() -> None:
    slept: list[float] = []
    client = _ScriptedClient([_response(429), _response(429), _response(200)])
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    # No Retry-After -> bounded exponential backoff (base * 2**(attempt-1)), NOT the full box.
    assert slept == [_BASE_S, _BASE_S * 2]
    assert len(client.calls) == 3


def test_no_retry_after_backoff_is_capped() -> None:
    # The bounded backoff must never exceed the cap, however many retries deep. With a cap below
    # the base's first step, every wait collapses to the cap — the no-hang guarantee.
    slept: list[float] = []
    client = _ScriptedClient([_response(429), _response(429), _response(200)])
    transport = _transport(client, slept, backoff_base_s=1000.0)
    cap = _NO_RETRY_AFTER_BACKOFF_CAP_S

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [cap, cap]
    assert cap < 60.0  # the whole point: a no-header 429 costs seconds, never minutes


def test_retry_after_header_is_honoured_verbatim_within_the_box_bound() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(503, headers={"Retry-After": "7"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [7.0]  # the server's wait, verbatim — within the box bound, no jitter added


def test_retry_after_is_bounded_by_the_penalty_box() -> None:
    # A pathological Retry-After cannot wedge the walk: it is capped at penalty_box_s.
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(429, headers={"Retry-After": "99999"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [_BOX_S]  # clamped to the documented box, not the absurd header value


def test_http_date_retry_after_falls_back_to_bounded_backoff() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [_BASE_S]  # unparsable as seconds -> bounded backoff, NOT the box


def test_exhausted_retries_raise_with_status_code() -> None:
    slept: list[float] = []
    # max_retries=2 -> 1 initial attempt + 2 retries = 3 wire calls, then the labeled error.
    client = _ScriptedClient([_response(429)] * 3)
    transport = _transport(client, slept, max_retries=2)

    with pytest.raises(CpRestTransportError) as excinfo:
        transport.get("/some/path")
    assert excinfo.value.status_code == 429
    assert len(client.calls) == 3
    assert slept == [_BASE_S, _BASE_S * 2]  # bounded backoff, no sleep after the final failure


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
