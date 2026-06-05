"""Tests for DeribitTransport — mocked HTTP, no network."""

from __future__ import annotations

import httpx
import pytest
from algotrading.infra_deribit.connectivity.deribit_transport import (
    DeribitSession,
    DeribitTransport,
)


def _make_transport(response_body: dict) -> DeribitTransport:
    """Build a DeribitTransport backed by an httpx mock that returns response_body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return DeribitTransport(client=client)


class TestDeribitTransportGet:
    def test_returns_result_field(self):
        transport = _make_transport(
            {"result": [{"instrument_name": "BTC-27JUN25-100000-C"}], "jsonrpc": "2.0"}
        )
        result = transport.get("/public/get_instruments", {"currency": "BTC", "kind": "option"})
        assert isinstance(result, list)
        assert result[0]["instrument_name"] == "BTC-27JUN25-100000-C"

    def test_raises_on_api_error(self):
        transport = _make_transport({"error": {"code": 10001, "message": "unauthorized"}})
        with pytest.raises(RuntimeError, match="Deribit API error"):
            transport.get("/public/get_instruments")

    def test_raises_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal server error")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        transport = DeribitTransport(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            transport.get("/public/get_instruments")

    def test_get_without_result_field_returns_whole_body(self):
        body = {"some_key": "some_value"}
        transport = _make_transport(body)
        result = transport.get("/public/ping")
        assert result == body


class TestDeribitSession:
    def test_context_manager_provides_transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": "ok"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with DeribitSession(client=client) as session:
            assert session.transport is not None
            result = session.transport.get("/public/ping")
            assert result == "ok"
        assert session.transport is None

    def test_transport_closed_on_exit(self):
        closed = []

        class _TrackingClient(httpx.Client):
            def close(self):
                closed.append(True)
                super().close()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": []})

        client = _TrackingClient(transport=httpx.MockTransport(handler))
        with DeribitSession(client=client):
            pass
        assert closed == [True]
