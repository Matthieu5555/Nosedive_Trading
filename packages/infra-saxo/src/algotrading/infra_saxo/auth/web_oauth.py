"""Saxo web-app OAuth protocol: build the authorize URL and exchange a code for tokens.

The Saxo authorization-code flow (confidential client, server-side redirect) is broker-specific
knowledge: which host serves the authorize/token endpoints per environment, and the exact request
shape. That knowledge belongs to this leaf, not to a frontend router. A caller (the app's OAuth
endpoint) owns its own CSRF state and redirect responses, and supplies the client credentials and
its callback URL; this module owns the Saxo wire protocol.
"""

from __future__ import annotations

import urllib.parse

from ..config import authorize_url_for, token_url_for


def build_authorize_url(*, state: str, env: str, client_id: str, redirect_uri: str) -> str:
    """Build the Saxo authorization URL for ``env`` with a caller-supplied CSRF ``state`` token.

    The host is resolved for ``env`` so the authorize call targets the same gateway the token
    exchange will use.
    """
    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{authorize_url_for(env)}?{params}"


def exchange_code_for_tokens(
    *,
    code: str,
    redirect_uri: str,
    env: str,
    client_id: str,
    client_secret: str,
    timeout: float = 15.0,
) -> dict:
    """Exchange an authorization ``code`` for tokens at the Saxo token endpoint for ``env``.

    Returns the parsed JSON token body (``access_token``, ``refresh_token``, ``expires_in``, ...).
    Raises on a non-2xx response. ``httpx`` is imported lazily so importing this leaf never pulls
    the HTTP stack into unrelated paths.
    """
    import httpx  # noqa: PLC0415 — deferred to keep httpx off unrelated import paths

    resp = httpx.post(
        token_url_for(env),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
