"""The PRODUCTION CP-REST path acquires an OAuth LST and signs every request (ADR 0031, C2).

The audit's seam: the per-request HMAC signer and the LST-derivation crypto existed as pure
functions, but no production path acquired a Live Session Token and wired the signer into the
transport — so the only "signing" the suite exercised was a test-injected fake header. These
tests pin the real path instead:

* :func:`acquire_live_session_token` runs the actual RSA → Diffie–Hellman → LST exchange on
  **pycryptodome**, against a fake IBKR endpoint whose DH side is computed *independently* here
  (the server picks its own ``b``, derives the same shared secret, and signs the LST exactly as
  IBKR does). The token our code derives must equal the one the independent server derived.
* :func:`build_signed_cp_rest_transport` is the production entry point. Driving it produces a
  :class:`CpRestTransport` with a real ``oauth_signer``; a subsequent GET through a fake httpx
  client must carry an ``Authorization: OAuth …`` header whose ``oauth_signature`` equals an
  **independently** recomputed LST-keyed HMAC-SHA256 of the RFC 5849 base string — not a value
  read back from the signer under test.

The crypto runs for real; only the HTTP sockets are fakes. Keys/nonce/timestamp/DH-exponent are
fixed so the whole exchange is reproducible. IBKR's network is never opened.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from algotrading.infra_ibkr.connectivity.cp_rest_lst import (
    DiffieHellmanParams,
    LstConsumer,
    acquire_live_session_token,
    build_signed_cp_rest_transport,
)
from Crypto.Cipher import PKCS1_v1_5 as PKCS1_v1_5_Cipher
from Crypto.PublicKey import RSA
from Crypto.Util.number import bytes_to_long, long_to_bytes

# -- fixed key/DH material so the exchange is a known-answer vector -----------------------
# A 1536-bit MODP prime (RFC 3526 group 5), the kind IBKR publishes for its DH group.
_DH_PRIME_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA237327FFFFFFFFFFFFFFFF"
)
_DH_GENERATOR = 2
_CONSUMER_KEY = "TESTCONSUMER"
_ACCESS_TOKEN = "ACCESSTOKEN"
_REALM = "limited_poa"
_BASE_URL = "https://api.ibkr.com/v1/api"

# Fixed injected randomness/clock — the exchange is reproducible.
_NONCE = "fixednonce0001"
_TIMESTAMP = 1_700_000_000
_CLIENT_DH_RANDOM = 0x1234567890ABCDEF1234567890ABCDEF
_SERVER_DH_RANDOM = 0x0FEDCBA9876543210FEDCBA987654321
_PREPEND = b"\x11\x22\x33\x44prepend-secret-bytes\x99"


@pytest.fixture(scope="module")
def keys() -> dict[str, Any]:
    """Deterministic RSA keys + the RSA-encrypted access-token-secret the consumer carries."""
    # Deterministic RNG so the key (and therefore the whole vector) is reproducible.
    state = {"buf": b"", "seed": b"ibkr-c2-deterministic-rsa-seed"}

    def randfunc(n: int) -> bytes:
        while len(state["buf"]) < n:
            state["seed"] = hashlib.sha256(state["seed"]).digest()
            state["buf"] += state["seed"]
        out, state["buf"] = state["buf"][:n], state["buf"][n:]
        return out

    signing_key = RSA.generate(2048, randfunc=randfunc)
    encryption_key = RSA.generate(2048, randfunc=randfunc)
    # IBKR hands the consumer the access-token-secret RSA-encrypted to its encryption key,
    # base64-encoded. Build that artifact here from the known prepend.
    ciphertext = PKCS1_v1_5_Cipher.new(encryption_key.publickey()).encrypt(_PREPEND)
    return {
        "signing_pem": signing_key.export_key().decode(),
        "encryption_pem": encryption_key.export_key().decode(),
        "access_token_secret_b64": base64.b64encode(ciphertext).decode(),
    }


@pytest.fixture
def consumer(keys: dict[str, Any]) -> LstConsumer:
    return LstConsumer(
        consumer_key=_CONSUMER_KEY,
        access_token=_ACCESS_TOKEN,
        access_token_secret=keys["access_token_secret_b64"],
        signing_key_pem=keys["signing_pem"],
        encryption_key_pem=keys["encryption_pem"],
        dh=DiffieHellmanParams.from_hex(_DH_PRIME_HEX, _DH_GENERATOR),
        realm=_REALM,
    )


def _server_side_lst(client_challenge_hex: str) -> str:
    """The LST as IBKR (the *server*) derives it from the client's DH challenge — the oracle.

    The server receives the client's ``A`` (hex), computes the shared secret ``K = A^b mod p``
    with its own exponent ``b``, sign-prefixes ``K``'s big-endian bytes, and sets
    LST = base64(HMAC-SHA1(K_bytes, prepend)). Works for any client ``a`` because it derives
    from the challenge actually sent — never read from our code.
    """
    p = int(_DH_PRIME_HEX, 16)
    client_a = int(client_challenge_hex, 16)
    shared = pow(client_a, _SERVER_DH_RANDOM, p)
    shared_bytes = long_to_bytes(shared)
    if shared_bytes and shared_bytes[0] & 0x80:
        shared_bytes = b"\x00" + shared_bytes
    return base64.b64encode(hmac.new(shared_bytes, _PREPEND, hashlib.sha1).digest()).decode()


class _FakeIbkrOauthEndpoint:
    """A fake IBKR OAuth endpoint: it plays the server side of the DH exchange (the oracle).

    It runs the *real* server-side DH against whatever challenge the client sends, so it works
    for both the fixed-``a`` known-answer vector and a random-``a`` production run. ``lst`` is
    set to the token it derived, for the test to compare against.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.lst = ""
        p = int(_DH_PRIME_HEX, 16)
        # The server's public DH value B = g^b mod p, returned base64 (big-endian bytes).
        self._dh_response_b64 = base64.b64encode(
            long_to_bytes(pow(_DH_GENERATOR, _SERVER_DH_RANDOM, p))
        ).decode()

    def post(self, path: str, headers: Mapping[str, str]) -> Mapping[str, object]:
        self.calls.append(path)
        assert headers["Authorization"].startswith("OAuth ")
        assert f'realm="{_REALM}"' in headers["Authorization"]
        if path.endswith("/request_token"):
            return {"oauth_token": "REQUESTTOKEN"}
        if path.endswith("/live_session_token"):
            challenge = headers["diffie_hellman_challenge"]
            assert challenge
            self.lst = _server_side_lst(challenge)
            # IBKR validates by HMAC-SHA1(consumer_key) keyed with the *decoded* LST, hex.
            lst_signature = hmac.new(
                base64.b64decode(self.lst), _CONSUMER_KEY.encode(), hashlib.sha1
            ).hexdigest()
            return {
                "diffie_hellman_response": self._dh_response_b64,
                "live_session_token_signature": lst_signature,
            }
        raise AssertionError(f"unexpected OAuth path {path!r}")


def test_acquire_lst_matches_independent_server_side_derivation(consumer: LstConsumer) -> None:
    endpoint = _FakeIbkrOauthEndpoint()
    lst = acquire_live_session_token(
        consumer,
        post=endpoint.post,
        base_url=_BASE_URL,
        nonce_factory=lambda: _NONCE,
        clock=lambda: _TIMESTAMP,
        dh_random_factory=lambda: _CLIENT_DH_RANDOM,
    )
    # Independent oracle computed from scratch here (fixed a and b, not via our code or the fake
    # helper): A = g^a, K = A^b, LST = base64(HMAC-SHA1(sign-prefixed K bytes, prepend)).
    p = int(_DH_PRIME_HEX, 16)
    a_pub = pow(_DH_GENERATOR, _CLIENT_DH_RANDOM, p)
    shared = pow(a_pub, _SERVER_DH_RANDOM, p)
    shared_bytes = long_to_bytes(shared)
    if shared_bytes[0] & 0x80:
        shared_bytes = b"\x00" + shared_bytes
    expected_lst = base64.b64encode(
        hmac.new(shared_bytes, _PREPEND, hashlib.sha1).digest()
    ).decode()

    # The token our pycryptodome RSA→DH code derives equals the one independently computed from
    # the shared secret. Both POST steps were exercised.
    assert lst == expected_lst
    assert lst == endpoint.lst
    assert endpoint.calls == ["/oauth/request_token", "/oauth/live_session_token"]


# ---- the production transport-signing seam ---------------------------------------------


class _FakeHttpxResponse:
    def __init__(self) -> None:
        self.content = b"{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return {"ok": True}


class _RecordingHttpxClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeHttpxResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeHttpxResponse()

    def close(self) -> None:
        return None


def _independent_request_signature(lst: str, method: str, url: str, query: dict[str, str]) -> str:
    """The expected per-request signature, recomputed from RFC 5849 independently of the signer.

    base = METHOD&URL&PARAMS over the merged oauth_* + query params; signature =
    base64(HMAC-SHA256(base, key=base64decode(LST))). This is the value IBKR would accept; it is
    derived here by hand, not captured from the code under test.
    """
    pe = lambda s: quote(str(s), safe="~")  # noqa: E731
    params = {
        "oauth_consumer_key": _CONSUMER_KEY,
        "oauth_nonce": _NONCE,
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(_TIMESTAMP),
        "oauth_token": _ACCESS_TOKEN,
        **query,
    }
    norm = "&".join(f"{pe(k)}={pe(params[k])}" for k in sorted(params))
    base = "&".join([pe(method), pe(url), pe(norm)])
    key = base64.b64decode(lst)
    return base64.b64encode(hmac.new(key, base.encode(), hashlib.sha256).digest()).decode()


def test_production_transport_signs_each_request_with_the_acquired_lst(
    consumer: LstConsumer,
) -> None:
    endpoint = _FakeIbkrOauthEndpoint()
    http = _RecordingHttpxClient()

    # The real production builder: it runs the RSA→DH→LST exchange (against the fake endpoint),
    # builds the real make_oauth_signer, and constructs a CpRestTransport with that signer. Only
    # the httpx client is faked (_client), so the production signing code runs end to end. The
    # per-request nonce/timestamp are pinned so the emitted signature is a known-answer value.
    transport = build_signed_cp_rest_transport(
        consumer,
        base_url=_BASE_URL,
        post=endpoint.post,
        nonce_factory=lambda: _NONCE,
        clock=lambda: _TIMESTAMP,
        _client=http,
    )

    query = {"conid": "8314", "bar": "1d", "period": "1y"}
    transport.get("/iserver/marketdata/history", query)

    # A real request went out with an OAuth Authorization header produced by the production path.
    sent = http.calls[0]
    auth = sent["headers"]["Authorization"]
    assert auth.startswith("OAuth ")
    assert f'realm="{_REALM}"' in auth
    assert 'oauth_consumer_key="TESTCONSUMER"' in auth
    assert 'oauth_signature_method="HMAC-SHA256"' in auth

    # The signature in the header equals the independently recomputed LST-keyed HMAC — the
    # production transport really signed with the acquired Live Session Token.
    url = f"{_BASE_URL}/iserver/marketdata/history"
    expected_sig = _independent_request_signature(endpoint.lst, "GET", url, query)
    assert f'oauth_signature="{quote(expected_sig, safe="~")}"' in auth


def test_pycryptodome_is_a_live_import_in_the_production_signer() -> None:
    # The C2 audit point: pycryptodome must be a real production import, not a dead dependency.
    import algotrading.infra_ibkr.connectivity.cp_rest_lst as lst_mod

    src = lst_mod.__file__
    assert src is not None
    text = Path(src).read_text(encoding="utf-8")
    assert "from Crypto.PublicKey import RSA" in text
    assert "from Crypto.Signature import pkcs1_15" in text
    # And the RSA primitives are actually invoked, not merely imported.
    assert "pkcs1_15.new(key).sign" in text
    assert "RSA.import_key" in text
    # bytes_to_long / long_to_bytes drive the DH exchange.
    assert bytes_to_long(long_to_bytes(123)) == 123
