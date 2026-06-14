"""The booking write-barrier password gate — scrypt verification, fail-closed.

Independent oracle: the gate hashes with ``hashlib.scrypt``; the test computes the *same* scrypt
digest directly from the standard library (the engine, not the gate's own round-trip) and asserts
the gate opens for the matching password and blocks for everything else. The constant-time
comparison and the closed set of labelled block reasons are pinned here.
"""

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

# The pinned scrypt parameters the gate uses (RFC 7914 interactive cost). The oracle below
# recomputes the digest with these exact values, independently of the gate's own helper.
_N, _R, _P, _DKLEN, _MAXMEM = 2**14, 8, 1, 32, 2**25
_PASSWORD = "correct horse battery staple"


def _independent_digest(password: str, salt: bytes) -> str:
    """The expected digest, computed straight from hashlib — the oracle for the gate."""
    raw = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM
    )
    return raw.hex()


def _env(password: str, salt: bytes) -> dict[str, str]:
    return {ENV_GATE_SALT: salt.hex(), ENV_GATE_HASH: _independent_digest(password, salt)}


def test_hash_password_matches_an_independent_scrypt_computation() -> None:
    salt = secrets.token_bytes(16)
    # hash_password must produce exactly the stdlib scrypt digest — no homegrown crypto.
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
        {},  # nothing configured
        {ENV_GATE_SALT: "abcd"},  # salt only
        {ENV_GATE_HASH: "abcd"},  # digest only
        {ENV_GATE_SALT: "", ENV_GATE_HASH: ""},  # present but blank
    ],
)
def test_an_unconfigured_gate_blocks(env: dict[str, str]) -> None:
    decision = verify_password(_PASSWORD, env)
    assert isinstance(decision, GateBlock)
    assert decision.reason == UNCONFIGURED_GATE


@pytest.mark.parametrize(
    "env",
    [
        {ENV_GATE_SALT: "zzzz", ENV_GATE_HASH: "abcd"},  # salt not hex
        {ENV_GATE_SALT: "abcd", ENV_GATE_HASH: "xyz!"},  # digest not hex
    ],
)
def test_a_malformed_gate_config_blocks(env: dict[str, str]) -> None:
    decision = verify_password(_PASSWORD, env)
    assert isinstance(decision, GateBlock)
    assert decision.reason == MALFORMED_GATE_CONFIG


def test_a_different_salt_does_not_verify_even_with_a_matching_password() -> None:
    # The salt is load-bearing: the same password under a different salt yields a different
    # digest, so a digest provisioned for salt A must not open under salt B.
    salt_a = secrets.token_bytes(16)
    salt_b = secrets.token_bytes(16)
    env = {ENV_GATE_SALT: salt_b.hex(), ENV_GATE_HASH: _independent_digest(_PASSWORD, salt_a)}
    decision = verify_password(_PASSWORD, env)
    assert isinstance(decision, GateBlock)
    assert decision.reason == WRONG_PASSWORD
