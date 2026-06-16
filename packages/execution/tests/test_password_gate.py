from __future__ import annotations

import hashlib
import secrets

import pytest
from algotrading.execution.booking import GateBlock, GateOpen
from algotrading.execution.booking.password_gate import (
    ABSENT_PASSWORD,
    ENV_GATE_HASH,
    ENV_GATE_SALT,
    MALFORMED_GATE_CONFIG,
    UNCONFIGURED_GATE,
    WRONG_PASSWORD,
    hash_password,
    verify_password,
)

_N, _R, _P, _DKLEN, _MAXMEM = 2**14, 8, 1, 32, 2**25
_PASSWORD = "correct horse battery staple"


def _independent_digest(password: str, salt: bytes) -> str:
    raw = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM
    )
    return raw.hex()


def _env(password: str, salt: bytes) -> dict[str, str]:
    return {ENV_GATE_SALT: salt.hex(), ENV_GATE_HASH: _independent_digest(password, salt)}


def test_hash_password_matches_an_independent_scrypt_computation() -> None:
    salt = secrets.token_bytes(16)
    assert hash_password(_PASSWORD, salt) == _independent_digest(_PASSWORD, salt)


def test_the_correct_password_opens_the_gate() -> None:
    salt = secrets.token_bytes(16)
    decision = verify_password(_PASSWORD, _env(_PASSWORD, salt))
    assert isinstance(decision, GateOpen)


def test_a_wrong_password_is_a_labelled_block() -> None:
    salt = secrets.token_bytes(16)
    decision = verify_password("wrong", _env(_PASSWORD, salt))
    assert isinstance(decision, GateBlock)
    assert decision.reason == WRONG_PASSWORD


@pytest.mark.parametrize("password", ["", "   ", "\t\n"])
def test_an_empty_or_whitespace_password_is_absent_password(password: str) -> None:
    salt = secrets.token_bytes(16)
    decision = verify_password(password, _env(_PASSWORD, salt))
    assert isinstance(decision, GateBlock)
    assert decision.reason == ABSENT_PASSWORD


@pytest.mark.parametrize(
    "env",
    [
        {},
        {ENV_GATE_SALT: "abcd"},
        {ENV_GATE_HASH: "abcd"},
        {ENV_GATE_SALT: "", ENV_GATE_HASH: ""},
    ],
)
def test_an_unconfigured_gate_blocks(env: dict[str, str]) -> None:
    decision = verify_password(_PASSWORD, env)
    assert isinstance(decision, GateBlock)
    assert decision.reason == UNCONFIGURED_GATE


@pytest.mark.parametrize(
    "env",
    [
        {ENV_GATE_SALT: "zzzz", ENV_GATE_HASH: "abcd"},
        {ENV_GATE_SALT: "abcd", ENV_GATE_HASH: "xyz!"},
    ],
)
def test_a_malformed_gate_config_blocks(env: dict[str, str]) -> None:
    decision = verify_password(_PASSWORD, env)
    assert isinstance(decision, GateBlock)
    assert decision.reason == MALFORMED_GATE_CONFIG


def test_a_different_salt_does_not_verify_even_with_a_matching_password() -> None:
    salt_a = secrets.token_bytes(16)
    salt_b = secrets.token_bytes(16)
    env = {ENV_GATE_SALT: salt_b.hex(), ENV_GATE_HASH: _independent_digest(_PASSWORD, salt_a)}
    decision = verify_password(_PASSWORD, env)
    assert isinstance(decision, GateBlock)
    assert decision.reason == WRONG_PASSWORD
