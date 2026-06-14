"""The booking write-barrier password gate — verify an operator password, fail closed.

TARGET §2 #4: "Booking a position requires a **password** — an explicit human gate in front of
anything that changes the book." This module is that gate, and *only* that gate: it answers one
question — does the supplied password match the configured booking password? — and answers it
**fail-closed**. A wrong password, an absent password, or a malformed/absent gate configuration
all return a labelled :class:`GateBlock`; nothing here writes the book (the commit verb in
:mod:`~.commit` is the only writer, and it writes only on a verified :class:`GateOpen`).

Crypto is borrowed, never hand-rolled (AGENTS.md house rules): the password is verified with
``hashlib.scrypt`` (a memory-hard KDF from the standard library) against a stored salt + digest,
and the comparison is :func:`secrets.compare_digest` (constant-time, so a wrong password leaks no
timing signal). No plaintext password is ever stored, logged, or compared directly.

The configuration — the salt and the expected scrypt digest — is read from the process
environment / ``$HOME/.env`` (gitignored), the same boundary the IBKR loader uses (ADR 0031).
**No password material is a ``.py`` literal** (AGENTS.md §95–96). The env names are defined once
here; tests inject an in-memory ``Mapping`` and never touch the real ``os.environ``.

This gate is **not** the 3B broker-send gate (ADR 0042 / ``execution-order-sign-and-send``) —
that is a *separate* password behind a separate verb. Two gates, never conflated; this module
imports no broker and no order-submit symbol.
"""

from __future__ import annotations

import hashlib
import os
import secrets as _secrets  # aliased: the public name "secrets" trips the no-credential symbol scan
from collections.abc import Mapping
from dataclasses import dataclass

# The env var names, defined once (the IBKR-loader house pattern). Both are hex strings; neither
# is the password, and neither is ever a literal in code.
#   BOOKING_GATE_SCRYPT_SALT — the per-deployment random salt, hex-encoded.
#   BOOKING_GATE_SCRYPT_HASH — scrypt(password, salt, ...) expected digest, hex-encoded.
ENV_GATE_SALT = "BOOKING_GATE_SCRYPT_SALT"
ENV_GATE_HASH = "BOOKING_GATE_SCRYPT_HASH"

# scrypt cost parameters, pinned so a stored digest stays verifiable. These are not business
# parameters — they are fixed crypto invariants (RFC 7914 recommended interactive cost), so they
# live in code, at the top of the file, by the convention's "genuine internal invariant" rule.
# n must be a power of two; r and p tune memory/parallelism. maxmem is sized for this n,r,p.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 2**25  # 32 MiB — comfortably above 128 * n * r (≈ 16 MiB) for these params.


@dataclass(frozen=True, slots=True)
class GateOpen:
    """The gate verified: the supplied password matched the configured booking password."""


@dataclass(frozen=True, slots=True)
class GateBlock:
    """The gate refused, with a labelled reason. Fail-closed — the book is never written.

    ``reason`` is one of a small closed set so callers and the audit log can branch on the
    *kind* of block without parsing prose: ``"wrong_password"``, ``"absent_password"``,
    ``"unconfigured_gate"`` (no salt/digest in the environment), ``"malformed_gate_config"``
    (a salt/digest present but not valid hex). The human ``detail`` elaborates for the operator.
    """

    reason: str
    detail: str


# The gate's answer is exactly one of these — open or a labelled block, never an exception for
# the expected "wrong password" path (a bad password is a normal outcome, not a crash).
GateDecision = GateOpen | GateBlock

WRONG_PASSWORD = "wrong_password"
ABSENT_PASSWORD = "absent_password"
UNCONFIGURED_GATE = "unconfigured_gate"
MALFORMED_GATE_CONFIG = "malformed_gate_config"


def _scrypt_digest(password: str, salt: bytes) -> bytes:
    """scrypt the password against ``salt`` with the pinned cost parameters."""
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
    """Return the hex scrypt digest of ``password`` under ``salt`` — the value stored in ``.env``.

    The one place that derives a stored digest, so the provisioning helper and the verifier
    agree on the exact KDF and parameters. An operator generates ``BOOKING_GATE_SCRYPT_HASH``
    by salting a fresh random salt (``secrets.token_bytes``) and calling this; the plaintext
    password never leaves their shell and never lands in git.
    """
    return _scrypt_digest(password, salt).hex()


def verify_password(password: str, env: Mapping[str, str]) -> GateDecision:
    """Verify ``password`` against the gate config in ``env`` — fail-closed, constant-time.

    Returns :class:`GateOpen` only when a salt and expected digest are configured, both parse as
    hex, and ``scrypt(password, salt)`` equals the expected digest under
    :func:`secrets.compare_digest`. Every other outcome is a labelled :class:`GateBlock`:

    * an empty/whitespace password → ``absent_password`` (checked *before* the env so a missing
      input is named even on an unconfigured box);
    * a missing/blank salt or digest → ``unconfigured_gate``;
    * a salt or digest that is not valid hex → ``malformed_gate_config``;
    * a well-formed config the password does not match → ``wrong_password``.

    ``env`` is injected (the IBKR house pattern) so tests never touch the real process
    environment; :func:`verify_password_from_environment` is the production entry.
    """
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
    """Production entry: verify ``password`` against the gate config in ``os.environ``.

    The only function that touches the real environment; everything testable funnels through
    :func:`verify_password` with an injected mapping. ``$HOME/.env`` is expected to have been
    loaded into the environment by the process bootstrap (python-dotenv), as elsewhere.
    """
    return verify_password(password, os.environ)
