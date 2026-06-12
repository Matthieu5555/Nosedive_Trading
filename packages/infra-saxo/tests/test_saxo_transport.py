"""Tests for SaxoTransport's single request core — mocked HTTP, no network.

Expected URLs are derived by hand from the documented gateway bases
(``https://gateway.saxobank.com/sim/openapi`` / ``.../openapi``) plus the endpoint path,
never from the code under test.
"""

from __future__ import annotations

import json

import httpx
import pytest
from algotrading.infra_saxo.connectivity.saxo_transport import (
    SaxoTransport,
    SaxoTransportError,
)


def _capture_transport(
    response: httpx.Response | None = None,
    *,
    token_fn=lambda: "tok",
) -> tuple[SaxoTransport, list[httpx.Request]]:
    """A SaxoTransport over a MockTransport, returning the captured requests."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response if response is not None else httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SaxoTransport(token_fn=token_fn, _client=client), requests


def test_get_builds_exact_sim_url_with_params_and_bearer() -> None:
    transport, requests = _capture_transport()
    body = transport.get("/trade/v1/optionschain/subscriptions", params={"a": "1"})
    assert body == {"ok": True}
    (request,) = requests
    # sim base + path + query, derived from the documented gateway URL
    assert (
        str(request.url)
        == "https://gateway.saxobank.com/sim/openapi/trade/v1/optionschain/subscriptions?a=1"
    )
    assert request.method == "GET"
    assert request.headers["Authorization"] == "Bearer tok"
    assert "content-type" not in request.headers  # GET carries no JSON body


def test_post_sends_json_body_and_content_type() -> None:
    transport, requests = _capture_transport()
    transport.post("/trade/v1/optionschain/subscriptions", {"ContextId": "ctx"})
    (request,) = requests
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {"ContextId": "ctx"}


def test_post_empty_response_body_returns_empty_dict() -> None:
    transport, _ = _capture_transport(httpx.Response(201, content=b""))
    assert transport.post("/x", {"a": 1}) == {}


def test_patch_and_delete_return_none() -> None:
    transport, requests = _capture_transport(httpx.Response(204, content=b""))
    assert transport.patch("/sub/1", {"win": 2}) is None
    assert transport.delete("/sub/1") is None
    assert [r.method for r in requests] == ["PATCH", "DELETE"]
    assert json.loads(requests[0].content) == {"win": 2}
    assert "content-type" not in requests[1].headers  # DELETE carries no body


def test_token_fn_consulted_per_request_so_rotation_applies() -> None:
    tokens = iter(["tok1", "tok2"])
    transport, requests = _capture_transport(token_fn=lambda: next(tokens))
    transport.get("/a")
    transport.get("/a")
    assert [r.headers["Authorization"] for r in requests] == ["Bearer tok1", "Bearer tok2"]


@pytest.mark.parametrize(
    ("verb", "call"),
    [
        ("GET", lambda t: t.get("/x")),
        ("POST", lambda t: t.post("/x", {})),
        ("PATCH", lambda t: t.patch("/x", {})),
        ("DELETE", lambda t: t.delete("/x")),
    ],
)
def test_http_error_wrapped_with_status_for_every_verb(verb, call) -> None:
    transport, _ = _capture_transport(httpx.Response(404, text="missing"))
    with pytest.raises(SaxoTransportError, match=f"{verb} 404"):
        call(transport)


def test_network_error_wrapped_in_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SaxoTransport(token_fn=lambda: "tok", _client=client)
    with pytest.raises(SaxoTransportError, match="GET failed"):
        transport.get("/x")


def test_live_base_url_used_when_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = SaxoTransport(
        token_fn=lambda: "tok", base_url=SaxoTransport.LIVE_BASE_URL, _client=client
    )
    transport.get("/ref/v1/instruments")
    assert str(requests[0].url) == "https://gateway.saxobank.com/openapi/ref/v1/instruments"


def test_streaming_url_per_environment() -> None:
    sim = SaxoTransport(token_fn=lambda: "t", _client=httpx.Client())
    live = SaxoTransport(
        token_fn=lambda: "t", base_url=SaxoTransport.LIVE_BASE_URL, _client=httpx.Client()
    )
    assert (
        sim.streaming_url("ctx")
        == "wss://sim-streaming.saxobank.com/sim/oapi/streaming/ws/connect?contextId=ctx"
    )
    assert (
        live.streaming_url("ctx")
        == "wss://live-streaming.saxobank.com/oapi/streaming/ws/connect?contextId=ctx"
    )
