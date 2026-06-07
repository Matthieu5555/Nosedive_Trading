"""The transport adds the OAuth signer's Authorization header when one is configured (ADR 0031).

No live Gateway: a fake httpx-like client records the request headers. With no signer the
transport is the unchanged ADR 0024 cookie path; with a signer every request carries the
``Authorization: OAuth …`` header the signer returns, computed over the method/url/query.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from algotrading.infra_ibkr.connectivity.cp_rest_transport import CpRestTransport


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse({"ok": True})

    def close(self) -> None:
        return None


def test_no_signer_sends_no_authorization_header() -> None:
    client = _FakeClient()
    transport = CpRestTransport(_client=client)
    transport.get("/iserver/marketdata/history", {"conid": "8314"})
    assert "headers" not in client.calls[0] or "Authorization" not in client.calls[0].get("headers", {})


def test_signer_is_invoked_with_method_url_query_and_header_is_added() -> None:
    seen: dict[str, Any] = {}

    def signer(method: str, url: str, query: Mapping[str, object] | None) -> dict[str, str]:
        seen["method"] = method
        seen["url"] = url
        seen["query"] = dict(query or {})
        return {"Authorization": 'OAuth oauth_consumer_key="ck"'}

    client = _FakeClient()
    transport = CpRestTransport(
        base_url="https://api.ibkr.com/v1/api", oauth_signer=signer, _client=client
    )
    transport.get("/iserver/marketdata/history", {"conid": "8314", "bar": "1d"})

    assert seen["method"] == "GET"
    assert seen["url"] == "https://api.ibkr.com/v1/api/iserver/marketdata/history"
    assert seen["query"] == {"conid": "8314", "bar": "1d"}
    assert client.calls[0]["headers"]["Authorization"] == 'OAuth oauth_consumer_key="ck"'
