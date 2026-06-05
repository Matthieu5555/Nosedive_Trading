"""Tests for the Saxo web-app OAuth helper (authorize URL + code exchange)."""

from __future__ import annotations

import urllib.parse
from unittest.mock import MagicMock, patch

from algotrading.infra_saxo.auth import build_authorize_url, exchange_code_for_tokens
from algotrading.infra_saxo.config import authorize_url_for, token_url_for


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


def test_exchange_posts_to_token_endpoint_and_returns_body() -> None:
    token_resp = MagicMock()
    token_resp.json.return_value = {"access_token": "AT", "refresh_token": "RT", "expires_in": 1200}
    token_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=token_resp) as post:
        body = exchange_code_for_tokens(
            code="CODE",
            redirect_uri="https://app/cb",
            env="live",
            client_id="CID",
            client_secret="SECRET",
        )

    assert body["access_token"] == "AT"
    called_url = post.call_args[0][0]
    assert called_url == token_url_for("live")
    sent = post.call_args[1]["data"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "CODE"
