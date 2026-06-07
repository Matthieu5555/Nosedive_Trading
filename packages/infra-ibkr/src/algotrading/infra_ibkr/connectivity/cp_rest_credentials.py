"""Read the IBKR Client-Portal OAuth registration artifacts from the environment (ADR 0031).

The LST exchange (:mod:`.cp_rest_lst`) and the per-request HMAC signer (:mod:`.cp_rest_oauth`)
are fully implemented but secret-free: every key, token, and DH parameter is *caller-supplied*
(the C7 no-hardcode discipline). This module is the thin loader the OAuth work flagged as
missing — the one place that reads those registration artifacts from ``.env`` / the process
environment and assembles them into the :class:`LstConsumer` the LST flow consumes, plus the
real ``httpx``-backed ``post`` the two unsigned-by-LST exchange endpoints need.

The credential boundary, stated once so an operator knows exactly what to place in ``.env``:

* ``IBKR_CP_CONSUMER_KEY``        — the registered consumer key.
* ``IBKR_CP_ACCESS_TOKEN``        — the individual-account access token (a fixed registration
  artifact, not fetched per run).
* ``IBKR_CP_ACCESS_TOKEN_SECRET`` — that access token's secret, base64-encoded and RSA-encrypted
  to the encryption key (the registration artifact IBKR issues).
* ``IBKR_CP_SIGNING_KEY_PEM``     — path to the consumer's *signing* RSA private-key PEM file.
* ``IBKR_CP_ENCRYPTION_KEY_PEM``  — path to the consumer's *encryption* RSA private-key PEM file.
* ``IBKR_CP_DH_PRIME``            — IBKR's published Diffie–Hellman prime (hex).
* ``IBKR_CP_DH_GENERATOR``        — the DH generator (optional; defaults to 2, IBKR's value).
* ``IBKR_CP_REALM``               — the OAuth realm (optional; defaults to ``limited_poa``).

The signing/encryption keys are passed as **file paths** (the PEM bytes never belong in an env
var or in git); the rest are values. Absent or blank credentials are not an error: the loader
returns ``None`` (a labeled, narrow "not configured" answer) so a non-secret runner degrades to
the empty-basket no-capture path rather than crashing. A *partial* configuration — some fields
present, others blank — IS an error, surfaced as a labeled :class:`CpOAuthError` naming the
missing field, because a half-configured credential set is an operator mistake, not a clean
"no credentials" environment.

No secret is a literal here; nothing is logged at value level. The HTTP ``post`` opens a real
``httpx`` socket, so it is injected into the LST flow only on the live path — every test passes
a fake endpoint and this module's :func:`load_lst_consumer` is exercised against an in-memory
environment mapping, never the real ``os.environ`` or the network.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from .cp_rest_lst import DiffieHellmanParams, LstConsumer
from .cp_rest_oauth import CpOAuthError

# The env var names, defined once. The two PEM keys are PATHS (the key bytes never live in an
# env var); everything else is a value. Generator/realm are optional with IBKR's defaults.
ENV_CONSUMER_KEY = "IBKR_CP_CONSUMER_KEY"
ENV_ACCESS_TOKEN = "IBKR_CP_ACCESS_TOKEN"
ENV_ACCESS_TOKEN_SECRET = "IBKR_CP_ACCESS_TOKEN_SECRET"
ENV_SIGNING_KEY_PEM = "IBKR_CP_SIGNING_KEY_PEM"
ENV_ENCRYPTION_KEY_PEM = "IBKR_CP_ENCRYPTION_KEY_PEM"
ENV_DH_PRIME = "IBKR_CP_DH_PRIME"
ENV_DH_GENERATOR = "IBKR_CP_DH_GENERATOR"
ENV_REALM = "IBKR_CP_REALM"

# The required artifacts — every one of these must be present (and non-blank) for a credentialed
# environment. The two optional ones (generator, realm) have IBKR defaults and are not counted.
_REQUIRED_ENV = (
    ENV_CONSUMER_KEY,
    ENV_ACCESS_TOKEN,
    ENV_ACCESS_TOKEN_SECRET,
    ENV_SIGNING_KEY_PEM,
    ENV_ENCRYPTION_KEY_PEM,
    ENV_DH_PRIME,
)

_DEFAULT_GENERATOR = 2
_DEFAULT_REALM = "limited_poa"


def _present(env: Mapping[str, str], name: str) -> bool:
    """Whether an env var is present and non-blank (whitespace-only counts as absent)."""
    return bool(env.get(name, "").strip())


def credentials_present(env: Mapping[str, str] | None = None) -> bool:
    """True only when **every** required IBKR CP OAuth artifact is present and non-blank.

    The single predicate the production wiring keys live-vs-empty on. A fully-blank environment
    is ``False`` (the clean no-capture path); a fully-populated one is ``True``. A *partial*
    environment is also ``False`` here — :func:`load_lst_consumer` is the one that turns a
    partial set into a labeled error, so the selection stays a simple boolean while a misconfig
    still fails loudly when an operator actually tries to load it.
    """
    resolved = os.environ if env is None else env
    return all(_present(resolved, name) for name in _REQUIRED_ENV)


def _read_pem(env: Mapping[str, str], name: str) -> str:
    """Read a PEM key from the file path held in env var ``name`` (the bytes never live in env)."""
    raw = env.get(name, "").strip()
    if not raw:
        raise CpOAuthError(f"missing OAuth credential {name!r} (PEM key file path)")
    path = Path(raw)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CpOAuthError(f"cannot read PEM file for {name!r} at {path}: {exc}") from exc
    if not text.strip():
        raise CpOAuthError(f"PEM file for {name!r} at {path} is empty")
    return text


def load_lst_consumer(env: Mapping[str, str] | None = None) -> LstConsumer | None:
    """Assemble an :class:`LstConsumer` from the environment, or ``None`` when unconfigured.

    Returns ``None`` when **no** required artifact is present (the clean, credential-free
    environment — the caller then runs the empty-basket no-capture path, exit 0). When *some*
    but not all required artifacts are present, raises a labeled :class:`CpOAuthError` naming
    the first missing one — a half-configured environment is an operator mistake, never silently
    treated as "no credentials". The two PEM keys are read from their file paths here; the rest
    are values. ``env`` is injectable so the gate drives an in-memory mapping, never the real
    ``os.environ`` or any real file outside a test fixture.
    """
    resolved = os.environ if env is None else env
    present = [name for name in _REQUIRED_ENV if _present(resolved, name)]
    if not present:
        return None
    missing = [name for name in _REQUIRED_ENV if not _present(resolved, name)]
    if missing:
        raise CpOAuthError(
            f"IBKR CP OAuth is partially configured: missing {missing!r} "
            f"(set every required artifact, or none, in .env)"
        )

    generator_raw = resolved.get(ENV_DH_GENERATOR, "").strip()
    try:
        generator = int(generator_raw) if generator_raw else _DEFAULT_GENERATOR
    except ValueError as exc:
        raise CpOAuthError(
            f"{ENV_DH_GENERATOR!r} must be an integer, got {generator_raw!r}"
        ) from exc

    return LstConsumer(
        consumer_key=resolved[ENV_CONSUMER_KEY].strip(),
        access_token=resolved[ENV_ACCESS_TOKEN].strip(),
        access_token_secret=resolved[ENV_ACCESS_TOKEN_SECRET].strip(),
        signing_key_pem=_read_pem(resolved, ENV_SIGNING_KEY_PEM),
        encryption_key_pem=_read_pem(resolved, ENV_ENCRYPTION_KEY_PEM),
        dh=DiffieHellmanParams.from_hex(resolved[ENV_DH_PRIME].strip(), generator),
        realm=resolved.get(ENV_REALM, "").strip() or _DEFAULT_REALM,
    )


def make_lst_http_post(base_url: str, *, timeout: float = 30.0, verify_tls: bool = True) -> Any:
    """Build the real ``httpx``-backed POST the two LST exchange endpoints need.

    :func:`acquire_live_session_token` injects a ``(path, headers) -> json`` POST for the
    ``/oauth/request_token`` and ``/oauth/live_session_token`` calls (these precede the LST and
    so are signed RSA-SHA256, not with the transport's per-request HMAC). This returns that
    callable, backed by a real ``httpx.Client`` against the hosted endpoint — the one place a
    socket opens. Every test passes a fake ``post`` instead, so this function is never called
    under the gate. ``verify_tls`` defaults on (the hosted ``api.ibkr.com``, not localhost).
    """
    client = httpx.Client(timeout=timeout, verify=verify_tls)
    root = base_url.rstrip("/")

    def post(path: str, headers: Mapping[str, str]) -> Mapping[str, object]:
        url = f"{root}/{path.lstrip('/')}"
        # The exchange endpoints carry their parameters in the Authorization header (and a
        # diffie_hellman_challenge header on step 2); the body is empty.
        response = client.post(url, headers=dict(headers))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise CpOAuthError(f"OAuth exchange POST {path!r} returned non-object: {payload!r}")
        return payload

    return post
