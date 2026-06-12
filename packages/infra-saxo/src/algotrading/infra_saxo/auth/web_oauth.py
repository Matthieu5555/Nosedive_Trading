"""Saxo web-app OAuth protocol: build the authorize URL and exchange a code for tokens.

The OAuth2 wire protocol itself is Authlib's job (``OAuth2Client.create_authorization_url`` /
``fetch_token``); what stays here is the Saxo-specific knowledge: which host serves the
authorize/token endpoints per environment, and that Saxo wants the client credentials in the
token-request body (``client_secret_post``). A caller (the app's OAuth endpoint) owns its own
CSRF state and redirect responses, and supplies the client credentials and its callback URL;
this module owns the Saxo binding.
"""

from __future__ import annotations

from typing import Any

from ..config import authorize_url_for, token_url_for

# Saxo accepts the client credentials in the token-request body (the shape the platform has
# always sent); Authlib's default is HTTP Basic, so the method is pinned explicitly.
_TOKEN_AUTH_METHOD = "client_secret_post"


def build_authorize_url(*, state: str, env: str, client_id: str, redirect_uri: str) -> str:
    """Build the Saxo authorization URL for ``env`` with a caller-supplied CSRF ``state`` token.

    The host is resolved for ``env`` so the authorize call targets the same gateway the token
    exchange will use.
    """
    from authlib.integrations.httpx_client import (  # type: ignore[import-untyped]  # noqa: PLC0415
        OAuth2Client,
    )

    with OAuth2Client(client_id=client_id, redirect_uri=redirect_uri) as client:
        url, _state = client.create_authorization_url(authorize_url_for(env), state=state)
    return str(url)


def exchange_code_for_tokens(
    *,
    code: str,
    redirect_uri: str,
    env: str,
    client_id: str,
    client_secret: str,
    timeout: float = 15.0,
    transport: Any | None = None,
) -> dict:
    """Exchange an authorization ``code`` for tokens at the Saxo token endpoint for ``env``.

    Returns the parsed token body (``access_token``, ``refresh_token``, ``expires_in``, ...,
    plus Authlib's computed ``expires_at``). Raises ``authlib.integrations.base_client.OAuthError``
    on an error response. ``transport`` is an optional ``httpx`` transport (test seam — e.g.
    ``httpx.MockTransport``). Authlib is imported lazily so importing this leaf never pulls the
    HTTP stack into unrelated paths.
    """
    from authlib.integrations.httpx_client import (  # noqa: PLC0415 — deferred, untyped (see above)
        OAuth2Client,
    )

    client_kwargs: dict[str, Any] = {"timeout": timeout}
    if transport is not None:
        client_kwargs["transport"] = transport
    with OAuth2Client(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        token_endpoint_auth_method=_TOKEN_AUTH_METHOD,
        **client_kwargs,
    ) as client:
        token = client.fetch_token(token_url_for(env), code=code)
    return dict(token)
