from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import quote

_UNRESERVED_SAFE = "~"

SIGNATURE_METHOD = "HMAC-SHA256"


class CpOAuthError(Exception):
    pass


def percent_encode(value: object) -> str:
    return quote(str(value), safe=_UNRESERVED_SAFE)


def _normalized_parameters(params: Mapping[str, object]) -> str:
    encoded = sorted(
        (percent_encode(key), percent_encode(value)) for key, value in params.items()
    )
    return "&".join(f"{key}={value}" for key, value in encoded)


def signature_base_string(method: str, url: str, params: Mapping[str, object]) -> str:
    return "&".join(
        (
            percent_encode(method.upper()),
            percent_encode(url),
            percent_encode(_normalized_parameters(params)),
        )
    )


def sign_hmac_sha256(base_string: str, *, live_session_token: str) -> str:
    if not live_session_token:
        raise CpOAuthError("missing live session token (expired or never established)")
    try:
        key = base64.b64decode(live_session_token, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CpOAuthError(f"live session token is not valid base64: {exc}") from exc
    digest = hmac.new(key, base_string.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def sign_hmac_sha256_raw_key(base_string: str, *, key: str) -> str:
    if not key:
        raise CpOAuthError("empty signing key")
    digest = hmac.new(key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


@dataclass(frozen=True, slots=True)
class OAuthCredentials:

    consumer_key: str
    live_session_token: str
    access_token: str = ""

    def __post_init__(self) -> None:
        if not self.consumer_key:
            raise CpOAuthError("missing consumer key")
        if not self.live_session_token:
            raise CpOAuthError("missing live session token (expired or never established)")


def oauth_protocol_params(
    credentials: OAuthCredentials, *, nonce: str, timestamp: int
) -> dict[str, str]:
    params = {
        "oauth_consumer_key": credentials.consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": SIGNATURE_METHOD,
        "oauth_timestamp": str(timestamp),
    }
    if credentials.access_token:
        params["oauth_token"] = credentials.access_token
    return params


def sign_request(
    credentials: OAuthCredentials,
    *,
    method: str,
    url: str,
    query_params: Mapping[str, object] | None = None,
    nonce: str,
    timestamp: int,
) -> dict[str, str]:
    protocol = oauth_protocol_params(credentials, nonce=nonce, timestamp=timestamp)
    all_params: dict[str, object] = dict(protocol)
    if query_params:
        all_params.update(query_params)
    base = signature_base_string(method, url, all_params)
    protocol["oauth_signature"] = sign_hmac_sha256(
        base, live_session_token=credentials.live_session_token
    )
    return protocol


def authorization_header(signed_params: Mapping[str, str], *, realm: str = "") -> str:
    parts = sorted(
        f'{percent_encode(key)}="{percent_encode(value)}"'
        for key, value in signed_params.items()
        if key.startswith("oauth_")
    )
    if realm:
        parts = [f'realm="{percent_encode(realm)}"', *parts]
    return "OAuth " + ", ".join(parts)


def make_oauth_signer(
    credentials: OAuthCredentials,
    *,
    realm: str = "",
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    clock: Callable[[], int] = lambda: int(time.time()),
) -> Callable[[str, str, Mapping[str, object] | None], dict[str, str]]:

    def signer(
        method: str, url: str, query_params: Mapping[str, object] | None
    ) -> dict[str, str]:
        signed = sign_request(
            credentials,
            method=method,
            url=url,
            query_params=query_params,
            nonce=nonce_factory(),
            timestamp=clock(),
        )
        return {"Authorization": authorization_header(signed, realm=realm)}

    return signer
