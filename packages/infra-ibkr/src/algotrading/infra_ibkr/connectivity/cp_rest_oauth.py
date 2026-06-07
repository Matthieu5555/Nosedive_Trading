"""OAuth 1.0a signing for the IBKR Client Portal REST API (ADR 0031).

The Client Portal Web API can be driven unattended with **OAuth 1.0a** (a Live Session
Token, ~24h) instead of the interactive browser login the bare Gateway requires. This
module is the in-house signer (referencing ``ibind``'s implementation, not depending on
it) built on **pycryptodome** — no second REST client library is added (ADR 0031 §2).

What it owns is exactly the cryptographic core of OAuth 1.0a, kept pure so it has a
hand-computable independent oracle (RFC 5849):

* :func:`signature_base_string` — the percent-encoded ``METHOD&URL&PARAMS`` triple every
  OAuth 1.0a signature is computed over (RFC 5849 §3.4.1).
* :func:`sign_hmac_sha256` — the per-request signature: ``HMAC-SHA256(base, key)``,
  base64-encoded, where the key is the Live Session Token (RFC 5849 §3.4.2, IBKR's
  ``HMAC-SHA256`` variant). This is the signature carried on every history request once
  the LST is in hand.
* :func:`authorization_header` — assembles the ``Authorization: OAuth …`` header from the
  signed protocol parameters, percent-encoded and quoted per RFC 5849 §3.5.1.

What it deliberately does **not** do: fetch the Live Session Token from IBKR (the RSA
request-token / DH key-exchange dance) or read any secret. Secrets — consumer key/secret,
the LST, the encryption/signing key paths — are caller-supplied (from ``.env`` / a
validated config object), never literals here (the C7 no-hardcode discipline). A request
signed with a missing or empty token raises a **labeled** :class:`CpOAuthError`, not a
bare ``KeyError``, so an expired/absent token reads as an auth failure in the log.

No clock and no nonce source is read here: ``timestamp`` and ``nonce`` are injected, so a
signature is a pure function of its inputs and a known-answer test is deterministic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

# RFC 3986 unreserved set: OAuth percent-encoding leaves only A-Z a-z 0-9 - . _ ~ unescaped
# (RFC 5849 §3.6). ``urllib.parse.quote`` with safe="~" matches this exactly once the default
# unreserved (letters/digits/_.-) is combined with the explicitly-kept tilde.
_UNRESERVED_SAFE = "~"

SIGNATURE_METHOD = "HMAC-SHA256"


class CpOAuthError(Exception):
    """An OAuth 1.0a signing step failed — a labeled auth error, never a bare exception.

    Raised for a missing/empty Live Session Token or consumer credential, so an
    expired or unconfigured token surfaces as a named auth failure the operator can act
    on rather than a cryptic ``KeyError``/``b64decode`` traceback.
    """


def percent_encode(value: object) -> str:
    """Percent-encode a value per RFC 5849 §3.6 (only unreserved chars left raw)."""
    return quote(str(value), safe=_UNRESERVED_SAFE)


def _normalized_parameters(params: Mapping[str, object]) -> str:
    """The sorted, percent-encoded ``k=v&k=v`` parameter string (RFC 5849 §3.4.1.3.2).

    Each key and value is percent-encoded, the pairs are sorted by encoded key (then
    value), and joined with ``&``. Sorting makes the base string order-independent in the
    caller's parameter dict, which is what lets a re-signed identical request reproduce
    the same signature.
    """
    encoded = sorted(
        (percent_encode(key), percent_encode(value)) for key, value in params.items()
    )
    return "&".join(f"{key}={value}" for key, value in encoded)


def signature_base_string(method: str, url: str, params: Mapping[str, object]) -> str:
    """The OAuth 1.0a signature base string ``METHOD&URL&PARAMS`` (RFC 5849 §3.4.1).

    ``method`` is upper-cased; ``url`` is the request URL without query/fragment; ``params``
    are every OAuth protocol parameter plus the request's own query parameters (the caller
    merges them). The three components are individually percent-encoded and joined with
    ``&``. This is the exact string a hand-computed oracle hashes, which is how the
    known-answer test pins the signer.
    """
    return "&".join(
        (
            percent_encode(method.upper()),
            percent_encode(url),
            percent_encode(_normalized_parameters(params)),
        )
    )


def sign_hmac_sha256(base_string: str, *, live_session_token: str) -> str:
    """Sign a base string with the Live Session Token: base64(HMAC-SHA256(base, LST)).

    IBKR's per-request signature keys the HMAC with the base64-decoded Live Session Token
    (the shared secret produced by the LST exchange) and signs the base string with
    SHA-256, then base64-encodes the digest (RFC 5849 §3.4.2, IBKR ``HMAC-SHA256``). An
    empty or non-base64 token is a labeled :class:`CpOAuthError`, not a raw decode crash.
    """
    if not live_session_token:
        raise CpOAuthError("missing live session token (expired or never established)")
    try:
        key = base64.b64decode(live_session_token, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CpOAuthError(f"live session token is not valid base64: {exc}") from exc
    digest = hmac.new(key, base_string.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def sign_hmac_sha256_raw_key(base_string: str, *, key: str) -> str:
    """Generic RFC 5849 HMAC-SHA256 signature with a raw (non-base64) string key.

    The textbook OAuth 1.0a signing key is ``percent_encode(consumer_secret)&
    percent_encode(token_secret)``; this signs a base string with that string key
    directly. Kept as the primitive a hand-computed RFC oracle checks against (the test
    derives the same key and digest by hand), separate from IBKR's base64-LST variant.
    """
    if not key:
        raise CpOAuthError("empty signing key")
    digest = hmac.new(key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    """The non-secret-bearing handle the signer needs, all caller-supplied.

    ``consumer_key`` identifies the registered consumer; ``live_session_token`` is the
    ~24h shared secret from the LST exchange (base64). Neither is a literal in this module
    — they come from ``.env`` / validated config (C7). An empty ``consumer_key`` or
    ``live_session_token`` is rejected so a half-configured signer fails loudly at
    construction, not mid-request.
    """

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
    """The OAuth 1.0a protocol parameters for a request (no signature yet).

    ``nonce`` and ``timestamp`` are injected (no clock, no random read here) so signing is
    deterministic and a known-answer test reproduces it. ``oauth_token`` is included only
    when an access token is present.
    """
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
    """Return the signed OAuth protocol parameters for one request.

    Merges the OAuth protocol parameters with the request's own query parameters into the
    signature base string (RFC 5849 §3.4.1.3), signs it with the Live Session Token, and
    returns the protocol parameters plus the computed ``oauth_signature``. The query
    parameters are *not* returned (the caller already has them); only the OAuth fields the
    ``Authorization`` header carries are.
    """
    protocol = oauth_protocol_params(credentials, nonce=nonce, timestamp=timestamp)
    all_params: dict[str, object] = dict(protocol)
    if query_params:
        all_params.update(query_params)
    base = signature_base_string(method, url, all_params)
    protocol["oauth_signature"] = sign_hmac_sha256(
        base, live_session_token=credentials.live_session_token
    )
    return protocol


def authorization_header(signed_params: Mapping[str, str]) -> str:
    """Assemble the ``Authorization: OAuth …`` header value (RFC 5849 §3.5.1).

    Every ``oauth_*`` parameter is percent-encoded and quoted, sorted for a stable header,
    and joined with ``, ``. Only the protocol parameters belong here — request query
    parameters stay in the URL/body, never in this header.
    """
    parts = sorted(
        f'{percent_encode(key)}="{percent_encode(value)}"'
        for key, value in signed_params.items()
        if key.startswith("oauth_")
    )
    return "OAuth " + ", ".join(parts)
