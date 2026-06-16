from __future__ import annotations

import hashlib
import os
import secrets as _secrets
from collections.abc import Mapping
from dataclasses import dataclass

ENV_GATE_SALT = "BOOKING_GATE_SCRYPT_SALT"
ENV_GATE_HASH = "BOOKING_GATE_SCRYPT_HASH"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 2**25


@dataclass(frozen=True, slots=True)
class GateOpen:
    pass


@dataclass(frozen=True, slots=True)
class GateBlock:

    reason: str
    detail: str


GateDecision = GateOpen | GateBlock

WRONG_PASSWORD = "wrong_password"
ABSENT_PASSWORD = "absent_password"
UNCONFIGURED_GATE = "unconfigured_gate"
MALFORMED_GATE_CONFIG = "malformed_gate_config"


def _scrypt_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )


def hash_password(password: str, salt: bytes) -> str:
    return _scrypt_digest(password, salt).hex()


def verify_password(password: str, env: Mapping[str, str]) -> GateDecision:
    if not password.strip():
        return GateBlock(ABSENT_PASSWORD, "no booking password was supplied")

    salt_hex = env.get(ENV_GATE_SALT, "").strip()
    digest_hex = env.get(ENV_GATE_HASH, "").strip()
    if not salt_hex or not digest_hex:
        return GateBlock(
            UNCONFIGURED_GATE,
            f"the booking gate is not configured ({ENV_GATE_SALT}/{ENV_GATE_HASH} absent)",
        )

    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return GateBlock(
            MALFORMED_GATE_CONFIG,
            f"{ENV_GATE_SALT}/{ENV_GATE_HASH} must be hex-encoded",
        )

    candidate = _scrypt_digest(password, salt)
    if _secrets.compare_digest(candidate, expected):
        return GateOpen()
    return GateBlock(WRONG_PASSWORD, "the supplied booking password did not match")


def verify_password_from_environment(password: str) -> GateDecision:
    return verify_password(password, os.environ)
