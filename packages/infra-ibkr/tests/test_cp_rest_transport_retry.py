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
    return httpx.Response(
        status,
        headers=headers,
        content=b'{"ok": true}' if status < 400 else b"",
        request=httpx.Request("GET", _URL),
    )


class _ScriptedClient:

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


_BOX_S = 123.0
_BASE_S = 0.5


def _transport(
    client: _ScriptedClient, slept: list[float], *, max_retries: int = 6, **kwargs: Any
) -> CpRestTransport:
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
    assert slept == [_BASE_S, _BASE_S * 2]
    assert len(client.calls) == 3


def test_no_retry_after_backoff_is_capped() -> None:
    slept: list[float] = []
    client = _ScriptedClient([_response(429), _response(429), _response(200)])
    transport = _transport(client, slept, backoff_base_s=1000.0)
    cap = _NO_RETRY_AFTER_BACKOFF_CAP_S

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [cap, cap]
    assert cap < 60.0


def test_retry_after_header_is_honoured_verbatim_within_the_box_bound() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(503, headers={"Retry-After": "7"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [7.0]


def test_retry_after_is_bounded_by_the_penalty_box() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(429, headers={"Retry-After": "99999"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [_BOX_S]


def test_http_date_retry_after_falls_back_to_bounded_backoff() -> None:
    slept: list[float] = []
    client = _ScriptedClient(
        [_response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), _response(200)]
    )
    transport = _transport(client, slept)

    assert transport.get("/some/path") == {"ok": True}
    assert slept == [_BASE_S]


def test_exhausted_retries_raise_with_status_code() -> None:
    slept: list[float] = []
    client = _ScriptedClient([_response(429)] * 3)
    transport = _transport(client, slept, max_retries=2)

    with pytest.raises(CpRestTransportError) as excinfo:
        transport.get("/some/path")
    assert excinfo.value.status_code == 429
    assert len(client.calls) == 3
    assert slept == [_BASE_S, _BASE_S * 2]


def test_non_retryable_status_fails_fast_with_status_code() -> None:
    slept: list[float] = []
    client = _ScriptedClient([_response(400)])
    transport = _transport(client, slept)

    with pytest.raises(CpRestTransportError) as excinfo:
        transport.get("/some/path")
    assert excinfo.value.status_code == 400
    assert slept == []
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
    assert nonces == ["nonce-0", "nonce-1"]
    assert [c["headers"]["Authorization"] for c in client.calls] == [
        'OAuth oauth_nonce="nonce-0"',
        'OAuth oauth_nonce="nonce-1"',
    ]
