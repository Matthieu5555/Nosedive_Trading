"""IBKR Client Portal OAuth 1.0a **Live Session Token** acquisition (ADR 0031 §2).

This is the half :mod:`.cp_rest_oauth` deliberately left out: the RSA-signed request-token call
and the Diffie–Hellman exchange that derive the ~24h **Live Session Token (LST)** from a
registered consumer's PEM keys, with *no interactive login*. The per-request HMAC signing in
:mod:`.cp_rest_oauth` keys off the LST this module produces; together they make the CP REST
session unattended-capable (ADR 0031 §2).

The flow, exactly as IBKR's individual-account OAuth 1.0a defines it (referencing ``ibind``'s
implementation, not depending on it — ADR 0031 §Consequences):

1. **Request token** — ``POST /oauth/request_token``, signed ``RSA-SHA256``: the OAuth base
   string is signed with the consumer's *signing* RSA private key (PKCS#1 v1.5 over SHA-256).
   IBKR returns an ``oauth_token`` (the temporary request token).
2. **Prepend** — the request-token response also carries no secret directly; the registered
   consumer's ``access_token_secret`` (issued at registration, supplied to us) is RSA-decrypted
   with the *encryption* private key (PKCS#1 v1.5) to yield the **prepend** bytes that salt the
   DH-derived key. (For an individual account the access token + its encrypted secret are fixed
   registration artifacts, so they are config, not fetched each run.)
3. **Diffie–Hellman exchange** — ``POST /oauth/live_session_token``: the client picks a random
   exponent ``a``, computes ``A = g^a mod p`` from IBKR's published DH ``prime``/``generator``,
   and sends ``A`` as ``diffie_hellman_challenge`` on a request signed ``RSA-SHA256`` with the
   *prepend* prefixed to the base string. IBKR replies with ``diffie_hellman_response`` (``B``)
   and a ``live_session_token_signature``.
4. **Derive the LST** — the shared secret ``K = B^a mod p`` (big-endian bytes); the LST is
   ``base64( HMAC-SHA1( key=K_bytes, msg=prepend_bytes ) )`` — IBKR's construction. It is
   validated by recomputing ``HMAC-SHA1`` of the consumer key under the *decoded* LST and
   checking it matches the returned ``live_session_token_signature`` (hex).

Everything cryptographic here runs on **pycryptodome** (``Crypto.PublicKey.RSA``,
``Crypto.Signature.pkcs1_15``, ``Crypto.Cipher.PKCS1_v1_5``, ``Crypto.Hash``,
``Crypto.Util.number``) — the dependency ADR 0031 declared. No secret is a literal: the PEM key
material, the consumer key, the access token + encrypted secret, and the DH parameters are all
caller-supplied from ``.env`` / validated config (the C7 no-hardcode discipline). The HTTP POST
is injected (``post`` callable), so the whole derivation is exercised in the gate against a fake
endpoint with a fixed test key — the real IBKR network is never opened in pytest.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from Crypto.Cipher import PKCS1_v1_5 as PKCS1_v1_5_Cipher
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Util.number import bytes_to_long, long_to_bytes

from .cp_rest_oauth import (
    CpOAuthError,
    OAuthCredentials,
    authorization_header,
    make_oauth_signer,
    signature_base_string,
)
from .cp_rest_transport import CpRestTransport

# IBKR's OAuth 1.0a LST endpoints (POSTed against the hosted CP Web API base, ADR 0031 §2).
_REQUEST_TOKEN_PATH = "/oauth/request_token"
_LIVE_SESSION_TOKEN_PATH = "/oauth/live_session_token"

# The request-token / LST steps are signed RSA-SHA256 (vs the per-request HMAC-SHA256). The
# header carries the method name so IBKR knows which signature scheme to verify.
_RSA_SIGNATURE_METHOD = "RSA-SHA256"

# An injected HTTP POST: ``(path, headers, ...) -> json``. The transport's signed POST is *not*
# reused here because these two calls use RSA signing, not the LST the transport keys off (the
# LST does not exist yet). Injected so the gate drives a fake IBKR endpoint, never the network.
LstHttpPost = Callable[[str, Mapping[str, str]], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class DiffieHellmanParams:
    """IBKR's published Diffie–Hellman parameters (the ``prime`` and ``generator``).

    Both are caller-supplied (from config, never a literal here): ``prime`` is IBKR's large DH
    modulus (hex) and ``generator`` is the DH generator (usually 2). They are the public group
    over which the LST key exchange runs.
    """

    prime: int
    generator: int

    @classmethod
    def from_hex(cls, prime_hex: str, generator: int = 2) -> DiffieHellmanParams:
        """Build from IBKR's hex-encoded prime (the form their registration page publishes)."""
        if not prime_hex:
            raise CpOAuthError("missing Diffie-Hellman prime")
        return cls(prime=int(prime_hex, 16), generator=generator)


@dataclass(frozen=True, slots=True)
class LstConsumer:
    """The registered-consumer key material the LST exchange needs — all caller-supplied.

    No field is a literal in this module (C7): every one is loaded from ``.env`` / validated
    config and handed in. ``signing_key_pem`` / ``encryption_key_pem`` are the consumer's RSA
    private keys (PEM text); ``access_token`` and ``access_token_secret`` are the fixed
    individual-account registration artifacts (the secret base64-encoded, RSA-encrypted to the
    encryption key); ``dh`` is IBKR's DH group; ``realm`` is the OAuth realm (``limited_poa``
    for an individual account).
    """

    consumer_key: str
    access_token: str
    access_token_secret: str
    signing_key_pem: str
    encryption_key_pem: str
    dh: DiffieHellmanParams
    realm: str = "limited_poa"

    def __post_init__(self) -> None:
        for name in (
            "consumer_key",
            "access_token",
            "access_token_secret",
            "signing_key_pem",
            "encryption_key_pem",
        ):
            if not getattr(self, name):
                raise CpOAuthError(f"missing OAuth LST field {name!r}")


def _rsa_sha256_signature(base_string: str, *, signing_key_pem: str) -> str:
    """RSA-SHA256 (PKCS#1 v1.5) signature of an OAuth base string, base64-encoded.

    The request-token and live-session-token steps are signed with the consumer's *signing*
    RSA private key, not the LST (which does not exist yet). pycryptodome computes the PKCS#1
    v1.5 signature over the SHA-256 digest of the base string; the result is base64 then
    percent-encoded into the ``oauth_signature`` field.
    """
    try:
        key = RSA.import_key(signing_key_pem)
    except (ValueError, IndexError, TypeError) as exc:
        raise CpOAuthError(f"signing key is not a valid RSA PEM: {exc}") from exc
    digest = SHA256.new(base_string.encode("utf-8"))
    signature = pkcs1_15.new(key).sign(digest)
    return base64.b64encode(signature).decode("ascii")


def _decrypt_prepend(access_token_secret_b64: str, *, encryption_key_pem: str) -> bytes:
    """RSA-decrypt the registered ``access_token_secret`` to the DH **prepend** bytes.

    IBKR issues the access-token secret base64-encoded and RSA-encrypted to the consumer's
    *encryption* public key. Decrypting it (PKCS#1 v1.5) yields the prepend that both salts the
    LST-derivation HMAC and prefixes the live-session-token request's base string.
    """
    try:
        key = RSA.import_key(encryption_key_pem)
    except (ValueError, IndexError, TypeError) as exc:
        raise CpOAuthError(f"encryption key is not a valid RSA PEM: {exc}") from exc
    try:
        ciphertext = base64.b64decode(access_token_secret_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CpOAuthError(f"access_token_secret is not valid base64: {exc}") from exc
    cipher = PKCS1_v1_5_Cipher.new(key)
    sentinel = secrets.token_bytes(16)
    prepend = cipher.decrypt(ciphertext, sentinel)
    if prepend == sentinel:
        raise CpOAuthError("could not RSA-decrypt access_token_secret with the encryption key")
    return prepend


def _oauth_params(consumer: LstConsumer, *, nonce: str, timestamp: int) -> dict[str, str]:
    """The RSA-signed-step OAuth protocol parameters (signature method ``RSA-SHA256``)."""
    return {
        "oauth_consumer_key": consumer.consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": _RSA_SIGNATURE_METHOD,
        "oauth_timestamp": str(timestamp),
        "oauth_token": consumer.access_token,
    }


def derive_live_session_token(
    response_b64: str, *, dh: DiffieHellmanParams, dh_random: int, prepend: bytes
) -> str:
    """Derive the base64 LST from IBKR's DH response, per IBKR's construction.

    ``response_b64`` is IBKR's ``diffie_hellman_response`` (``B``, base64 of its big-endian
    bytes); ``dh_random`` is the client exponent ``a``. The shared secret is ``K = B^a mod p``
    (big-endian bytes, sign-prefixed exactly as IBKR's reference does), and the LST is
    ``base64( HMAC-SHA1(key=K_bytes, msg=prepend) )``. A malformed response is a labeled error.
    """
    try:
        b_bytes = base64.b64decode(response_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CpOAuthError(f"diffie_hellman_response is not valid base64: {exc}") from exc
    b_value = bytes_to_long(b_bytes)
    shared = pow(b_value, dh_random, dh.prime)
    shared_bytes = long_to_bytes(shared)
    # IBKR sign-prefixes the shared secret: if the high bit is set, prepend a 0x00 byte so the
    # big-integer is unambiguously non-negative (matching their Java/`ibind` reference).
    if shared_bytes and shared_bytes[0] & 0x80:
        shared_bytes = b"\x00" + shared_bytes
    digest = hmac.new(shared_bytes, prepend, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_live_session_token(
    live_session_token: str, *, consumer_key: str, expected_signature_hex: str
) -> bool:
    """Validate the derived LST against IBKR's returned ``live_session_token_signature``.

    IBKR returns a hex HMAC-SHA1 of the consumer key keyed by the *decoded* LST; recomputing it
    and comparing (constant-time) confirms the DH exchange produced the same shared secret on
    both sides. A mismatch means the LST is unusable and is surfaced as such by the caller.
    """
    try:
        key = base64.b64decode(live_session_token, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CpOAuthError(f"derived live session token is not valid base64: {exc}") from exc
    recomputed = hmac.new(key, consumer_key.encode("utf-8"), hashlib.sha1).hexdigest()
    return hmac.compare_digest(recomputed, expected_signature_hex)


def acquire_live_session_token(
    consumer: LstConsumer,
    *,
    post: LstHttpPost,
    base_url: str,
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    clock: Callable[[], int] = lambda: int(time.time()),
    dh_random_factory: Callable[[], int] = lambda: bytes_to_long(secrets.token_bytes(32)),
) -> str:
    """Run the full RSA → DH exchange and return the base64 Live Session Token (ADR 0031 §2).

    Two RSA-SHA256-signed POSTs (``/oauth/request_token`` then ``/oauth/live_session_token``)
    via the injected ``post``, then the DH derivation. ``nonce_factory`` / ``clock`` /
    ``dh_random_factory`` are injected so a known-answer test fixes them and the whole exchange
    is reproducible against a fake endpoint — the network is never touched in the gate. The
    derived LST is validated against IBKR's returned signature; a mismatch raises a labeled
    :class:`CpOAuthError` rather than handing back an LST that would fail every later request.
    """
    prepend = _decrypt_prepend(
        consumer.access_token_secret, encryption_key_pem=consumer.encryption_key_pem
    )

    # Step 1 — request token, signed RSA-SHA256 (no DH challenge, no prepend yet).
    request_token_url = f"{base_url.rstrip('/')}{_REQUEST_TOKEN_PATH}"
    rt_params = _oauth_params(consumer, nonce=nonce_factory(), timestamp=clock())
    rt_base = signature_base_string("POST", request_token_url, rt_params)
    rt_params["oauth_signature"] = _rsa_sha256_signature(
        rt_base, signing_key_pem=consumer.signing_key_pem
    )
    rt_header = authorization_header(rt_params, realm=consumer.realm)
    rt_response = post(_REQUEST_TOKEN_PATH, {"Authorization": rt_header})
    request_token = rt_response.get("oauth_token")
    if not isinstance(request_token, str) or not request_token:
        raise CpOAuthError(f"request_token response missing oauth_token: {rt_response!r}")

    # Step 2 — live session token: send A = g^a mod p, base string prefixed with the prepend.
    dh_random = dh_random_factory()
    dh_challenge = pow(consumer.dh.generator, dh_random, consumer.dh.prime)
    challenge_hex = format(dh_challenge, "x")
    lst_url = f"{base_url.rstrip('/')}{_LIVE_SESSION_TOKEN_PATH}"
    lst_params = _oauth_params(consumer, nonce=nonce_factory(), timestamp=clock())
    # The request token replaces the access token on this step's signature.
    lst_params["oauth_token"] = request_token
    all_params: dict[str, object] = dict(lst_params)
    all_params["diffie_hellman_challenge"] = challenge_hex
    lst_base = prepend.hex() + signature_base_string("POST", lst_url, all_params)
    lst_params["oauth_signature"] = _rsa_sha256_signature(
        lst_base, signing_key_pem=consumer.signing_key_pem
    )
    lst_params["diffie_hellman_challenge"] = challenge_hex
    lst_header = authorization_header(
        {k: v for k, v in lst_params.items() if k.startswith("oauth_")},
        realm=consumer.realm,
    )
    lst_response = post(
        _LIVE_SESSION_TOKEN_PATH,
        {
            "Authorization": lst_header,
            "diffie_hellman_challenge": challenge_hex,
        },
    )
    dh_response = lst_response.get("diffie_hellman_response")
    lst_signature = lst_response.get("live_session_token_signature")
    if not isinstance(dh_response, str) or not isinstance(lst_signature, str):
        raise CpOAuthError(
            f"live_session_token response malformed (missing dh_response/signature): "
            f"{lst_response!r}"
        )

    live_session_token = derive_live_session_token(
        dh_response, dh=consumer.dh, dh_random=dh_random, prepend=prepend
    )
    if not validate_live_session_token(
        live_session_token,
        consumer_key=consumer.consumer_key,
        expected_signature_hex=lst_signature,
    ):
        raise CpOAuthError(
            "derived live session token failed IBKR signature validation "
            "(DH exchange mismatch — token would be rejected on every request)"
        )
    return live_session_token


def build_signed_cp_rest_transport(
    consumer: LstConsumer,
    *,
    base_url: str,
    post: LstHttpPost,
    timeout: float = 15.0,
    verify_tls: bool = True,
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    clock: Callable[[], int] = lambda: int(time.time()),
    _client: Any | None = None,
) -> CpRestTransport:
    """The production entry point: acquire the LST and return an OAuth-signed transport.

    This is the wiring the audit found missing — the path that turns key material into a live,
    signing :class:`CpRestTransport`. It runs the full RSA → DH → LST exchange
    (:func:`acquire_live_session_token`), wraps the derived token in :class:`OAuthCredentials`,
    builds the per-request HMAC signer (:func:`make_oauth_signer`), and constructs the transport
    with that signer injected — so every REST call the returned transport makes carries the
    OAuth ``Authorization`` header. ``verify_tls`` defaults **on** here (the hosted ``api.ibkr
    .com`` endpoint, not the self-signed localhost Gateway). ``post`` is the injected HTTP POST
    used only for the two unsigned-by-LST exchange calls; the gate passes a fake.

    ``nonce_factory`` / ``clock`` are the per-request OAuth nonce/timestamp sources, forwarded
    to :func:`make_oauth_signer`. They default to the real :mod:`secrets` / :func:`time.time`
    sources in production and are pinned to fixed values in the known-answer test so the produced
    signature is reproducible. ``_client`` is the same fake-httpx seam :class:`CpRestTransport`
    exposes: the gate passes a recording client so no socket opens while the *real* signer this
    builder constructs still runs.
    """
    live_session_token = acquire_live_session_token(
        consumer, post=post, base_url=base_url
    )
    credentials = OAuthCredentials(
        consumer_key=consumer.consumer_key,
        live_session_token=live_session_token,
        access_token=consumer.access_token,
    )
    signer = make_oauth_signer(
        credentials, realm=consumer.realm, nonce_factory=nonce_factory, clock=clock
    )
    return CpRestTransport(
        base_url=base_url,
        timeout=timeout,
        verify_tls=verify_tls,
        oauth_signer=signer,
        _client=_client,
    )
