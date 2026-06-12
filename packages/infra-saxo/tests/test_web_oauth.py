"""Tests for the Saxo web-app OAuth helper (authorize URL + code exchange, via Authlib)."""

from __future__ import annotations

import urllib.parse

import httpx
import pytest
from algotrading.infra_saxo.auth import build_authorize_url, exchange_code_for_tokens
from algotrading.infra_saxo.config import authorize_url_for, token_url_for
from authlib.integrations.base_client.errors import OAuthError


def _host(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def test_authorize_url_carries_oauth_params() -> None:
    url = build_authorize_url(
        state="STATE123", env="live", client_id="CID", redirect_uri="https://app/cb"
    )
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert query["response_type"] == "code"
    assert query["client_id"] == "CID"
    assert query["redirect_uri"] == "https://app/cb"
    assert query["state"] == "STATE123"


def test_authorize_and_token_share_the_same_gateway_per_env() -> None:
    # The C2 invariant: a sim authorize must not be paired with a live token endpoint.
    for env in ("sim", "live"):
        assert _host(authorize_url_for(env)) == _host(token_url_for(env))


def test_authorize_host_matches_requested_env() -> None:
    sim = build_authorize_url(state="s", env="sim", client_id="c", redirect_uri="r")
    live = build_authorize_url(state="s", env="live", client_id="c", redirect_uri="r")
    assert _host(sim) == _host(authorize_url_for("sim"))
    assert _host(live) == _host(authorize_url_for("live"))
    assert _host(sim) != _host(live)


def test_exchange_posts_authorization_code_grant_to_token_endpoint() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["form"] = dict(urllib.parse.parse_qsl(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 1200,
                "token_type": "Bearer",
            },
        )

    body = exchange_code_for_tokens(
        code="CODE",
        redirect_uri="https://app/cb",
        env="live",
        client_id="CID",
        client_secret="SECRET",
        transport=httpx.MockTransport(handler),
    )

    assert body["access_token"] == "AT"
    assert body["refresh_token"] == "RT"
    assert captured["url"] == token_url_for("live")
    # The RFC 6749 authorization_code grant shape, with the client credentials in the
    # body (client_secret_post) — exactly what Saxo expects and what was sent before.
    assert captured["form"]["grant_type"] == "authorization_code"
    assert captured["form"]["code"] == "CODE"
    assert captured["form"]["redirect_uri"] == "https://app/cb"
    assert captured["form"]["client_id"] == "CID"
    assert captured["form"]["client_secret"] == "SECRET"


def test_exchange_error_response_raises_oauth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "bad"})

    with pytest.raises(OAuthError, match="invalid_grant"):
        exchange_code_for_tokens(
            code="EXPIRED",
            redirect_uri="https://app/cb",
            env="sim",
            client_id="CID",
            client_secret="SECRET",
            transport=httpx.MockTransport(handler),
        )
