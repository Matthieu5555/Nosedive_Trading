from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import quote

import pytest
from algotrading.infra_ibkr.connectivity.cp_rest_oauth import (
    CpOAuthError,
    OAuthCredentials,
    authorization_header,
    oauth_protocol_params,
    percent_encode,
    sign_hmac_sha256,
    sign_hmac_sha256_raw_key,
    sign_request,
    signature_base_string,
)

_METHOD = "GET"
_URL = "https://api.ibkr.com/v1/api/iserver/marketdata/history"
_PARAMS = {
    "oauth_consumer_key": "TESTCONSUMER",
    "oauth_nonce": "abc123",
    "oauth_signature_method": "HMAC-SHA256",
    "oauth_timestamp": "1700000000",
    "oauth_token": "LSTTOKEN",
    "conid": "8314",
    "bar": "1d",
    "period": "1y",
}
_CONSUMER_SECRET = "CONSUMERSECRET"
_TOKEN_SECRET = "LSTSECRET"


def _oracle_base_string() -> str:
    pe = lambda s: quote(str(s), safe="~")  # noqa: E731
    norm = "&".join(f"{pe(k)}={pe(_PARAMS[k])}" for k in sorted(_PARAMS))
    return "&".join([pe(_METHOD), pe(_URL), pe(norm)])


def _oracle_signature(base: str) -> str:
    pe = lambda s: quote(str(s), safe="~")  # noqa: E731
    key = f"{pe(_CONSUMER_SECRET)}&{pe(_TOKEN_SECRET)}"
    return base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()
    ).decode()


def test_signature_base_string_matches_hand_computed_oracle() -> None:
    assert signature_base_string(_METHOD, _URL, _PARAMS) == _oracle_base_string()


def test_hmac_sha256_signature_matches_known_answer_vector() -> None:
    base = _oracle_base_string()
    expected = _oracle_signature(base)
    assert sign_hmac_sha256_raw_key(base, key=f"{percent_encode(_CONSUMER_SECRET)}&"
                                    f"{percent_encode(_TOKEN_SECRET)}") == expected
    assert expected == "B0ICVHFQsTQRXy+4hoAH80+RS3sUrL+fhZc/pdv2WPs="


def test_base_string_is_order_invariant_in_the_param_dict() -> None:
    reordered = dict(reversed(list(_PARAMS.items())))
    assert signature_base_string(_METHOD, _URL, reordered) == signature_base_string(
        _METHOD, _URL, _PARAMS
    )


def test_lst_keyed_signature_uses_base64_decoded_token() -> None:
    base = "GET&https%3A%2F%2Fexample%2Fx&a%3D1"
    raw_key = b"\x00\x01\x02\x03secret-bytes"
    lst = base64.b64encode(raw_key).decode()
    expected = base64.b64encode(
        hmac.new(raw_key, base.encode(), hashlib.sha256).digest()
    ).decode()
    assert sign_hmac_sha256(base, live_session_token=lst) == expected


def test_missing_token_raises_labeled_auth_error() -> None:
    with pytest.raises(CpOAuthError, match="live session token"):
        sign_hmac_sha256("base", live_session_token="")


def test_non_base64_token_raises_labeled_auth_error() -> None:
    with pytest.raises(CpOAuthError, match="not valid base64"):
        sign_hmac_sha256("base", live_session_token="!!!not-base64!!!")


def test_credentials_reject_empty_consumer_key_and_token() -> None:
    with pytest.raises(CpOAuthError, match="consumer key"):
        OAuthCredentials(consumer_key="", live_session_token="abc")
    with pytest.raises(CpOAuthError, match="live session token"):
        OAuthCredentials(consumer_key="ck", live_session_token="")


def test_sign_request_produces_a_verifiable_signature() -> None:
    raw_key = b"unattended-lst-key-bytes"
    lst = base64.b64encode(raw_key).decode()
    creds = OAuthCredentials(consumer_key="ck", live_session_token=lst, access_token="tok")
    signed = sign_request(
        creds,
        method="GET",
        url=_URL,
        query_params={"conid": "8314", "bar": "1d", "period": "1y"},
        nonce="nonce-1",
        timestamp=1_700_000_000,
    )
    all_params = {
        "oauth_consumer_key": "ck",
        "oauth_nonce": "nonce-1",
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": "1700000000",
        "oauth_token": "tok",
        "conid": "8314",
        "bar": "1d",
        "period": "1y",
    }
    pe = lambda s: quote(str(s), safe="~")  # noqa: E731
    norm = "&".join(f"{pe(k)}={pe(all_params[k])}" for k in sorted(all_params))
    base = "&".join([pe("GET"), pe(_URL), pe(norm)])
    expected = base64.b64encode(
        hmac.new(raw_key, base.encode(), hashlib.sha256).digest()
    ).decode()
    assert signed["oauth_signature"] == expected


def test_oauth_protocol_params_omit_token_when_absent() -> None:
    creds = OAuthCredentials(consumer_key="ck", live_session_token=base64.b64encode(b"k").decode())
    params = oauth_protocol_params(creds, nonce="n", timestamp=1)
    assert "oauth_token" not in params
    assert params["oauth_signature_method"] == "HMAC-SHA256"


def test_authorization_header_quotes_and_sorts_only_oauth_params() -> None:
    header = authorization_header(
        {
            "oauth_consumer_key": "ck",
            "oauth_signature": "sig+/=",
            "conid": "8314",
        }
    )
    assert header.startswith("OAuth ")
    assert 'oauth_consumer_key="ck"' in header
    assert 'oauth_signature="sig%2B%2F%3D"' in header
    assert "conid" not in header
