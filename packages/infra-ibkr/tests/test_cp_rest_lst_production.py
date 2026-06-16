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

_NONCE = "fixednonce0001"
_TIMESTAMP = 1_700_000_000
_CLIENT_DH_RANDOM = 0x1234567890ABCDEF1234567890ABCDEF
_SERVER_DH_RANDOM = 0x0FEDCBA9876543210FEDCBA987654321
_PREPEND = b"\x11\x22\x33\x44prepend-secret-bytes\x99"


@pytest.fixture(scope="module")
def keys() -> dict[str, Any]:
    state = {"buf": b"", "seed": b"ibkr-c2-deterministic-rsa-seed"}

    def randfunc(n: int) -> bytes:
        while len(state["buf"]) < n:
            state["seed"] = hashlib.sha256(state["seed"]).digest()
            state["buf"] += state["seed"]
        out, state["buf"] = state["buf"][:n], state["buf"][n:]
        return out

    signing_key = RSA.generate(2048, randfunc=randfunc)
    encryption_key = RSA.generate(2048, randfunc=randfunc)
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
    p = int(_DH_PRIME_HEX, 16)
    client_a = int(client_challenge_hex, 16)
    shared = pow(client_a, _SERVER_DH_RANDOM, p)
    shared_bytes = long_to_bytes(shared)
    if shared_bytes and shared_bytes[0] & 0x80:
        shared_bytes = b"\x00" + shared_bytes
    return base64.b64encode(hmac.new(shared_bytes, _PREPEND, hashlib.sha1).digest()).decode()


class _FakeIbkrOauthEndpoint:

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.lst = ""
        p = int(_DH_PRIME_HEX, 16)
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
    p = int(_DH_PRIME_HEX, 16)
    a_pub = pow(_DH_GENERATOR, _CLIENT_DH_RANDOM, p)
    shared = pow(a_pub, _SERVER_DH_RANDOM, p)
    shared_bytes = long_to_bytes(shared)
    if shared_bytes[0] & 0x80:
        shared_bytes = b"\x00" + shared_bytes
    expected_lst = base64.b64encode(
        hmac.new(shared_bytes, _PREPEND, hashlib.sha1).digest()
    ).decode()

    assert lst == expected_lst
    assert lst == endpoint.lst
    assert endpoint.calls == ["/oauth/request_token", "/oauth/live_session_token"]


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

    sent = http.calls[0]
    auth = sent["headers"]["Authorization"]
    assert auth.startswith("OAuth ")
    assert f'realm="{_REALM}"' in auth
    assert 'oauth_consumer_key="TESTCONSUMER"' in auth
    assert 'oauth_signature_method="HMAC-SHA256"' in auth

    url = f"{_BASE_URL}/iserver/marketdata/history"
    expected_sig = _independent_request_signature(endpoint.lst, "GET", url, query)
    assert f'oauth_signature="{quote(expected_sig, safe="~")}"' in auth


def test_pycryptodome_is_a_live_import_in_the_production_signer() -> None:
    import algotrading.infra_ibkr.connectivity.cp_rest_lst as lst_mod

    src = lst_mod.__file__
    assert src is not None
    text = Path(src).read_text(encoding="utf-8")
    assert "from Crypto.PublicKey import RSA" in text
    assert "from Crypto.Signature import pkcs1_15" in text
    assert "pkcs1_15.new(key).sign" in text
    assert "RSA.import_key" in text
    assert bytes_to_long(long_to_bytes(123)) == 123
