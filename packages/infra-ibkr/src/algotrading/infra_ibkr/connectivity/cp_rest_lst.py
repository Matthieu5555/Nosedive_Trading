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

_REQUEST_TOKEN_PATH = "/oauth/request_token"
_LIVE_SESSION_TOKEN_PATH = "/oauth/live_session_token"

_RSA_SIGNATURE_METHOD = "RSA-SHA256"

LstHttpPost = Callable[[str, Mapping[str, str]], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class DiffieHellmanParams:

    prime: int
    generator: int

    @classmethod
    def from_hex(cls, prime_hex: str, generator: int = 2) -> DiffieHellmanParams:
        if not prime_hex:
            raise CpOAuthError("missing Diffie-Hellman prime")
        return cls(prime=int(prime_hex, 16), generator=generator)


@dataclass(frozen=True, slots=True)
class LstConsumer:

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
    try:
        key = RSA.import_key(signing_key_pem)
    except (ValueError, IndexError, TypeError) as exc:
        raise CpOAuthError(f"signing key is not a valid RSA PEM: {exc}") from exc
    digest = SHA256.new(base_string.encode("utf-8"))
    signature = pkcs1_15.new(key).sign(digest)
    return base64.b64encode(signature).decode("ascii")


def _decrypt_prepend(access_token_secret_b64: str, *, encryption_key_pem: str) -> bytes:
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
    try:
        b_bytes = base64.b64decode(response_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CpOAuthError(f"diffie_hellman_response is not valid base64: {exc}") from exc
    b_value = bytes_to_long(b_bytes)
    shared = pow(b_value, dh_random, dh.prime)
    shared_bytes = long_to_bytes(shared)
    if shared_bytes and shared_bytes[0] & 0x80:
        shared_bytes = b"\x00" + shared_bytes
    digest = hmac.new(shared_bytes, prepend, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_live_session_token(
    live_session_token: str, *, consumer_key: str, expected_signature_hex: str
) -> bool:
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
    prepend = _decrypt_prepend(
        consumer.access_token_secret, encryption_key_pem=consumer.encryption_key_pem
    )

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

    dh_random = dh_random_factory()
    dh_challenge = pow(consumer.dh.generator, dh_random, consumer.dh.prime)
    challenge_hex = format(dh_challenge, "x")
    lst_url = f"{base_url.rstrip('/')}{_LIVE_SESSION_TOKEN_PATH}"
    lst_params = _oauth_params(consumer, nonce=nonce_factory(), timestamp=clock())
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
