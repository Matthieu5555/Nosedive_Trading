from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from .cp_rest_lst import DiffieHellmanParams, LstConsumer
from .cp_rest_oauth import CpOAuthError

ENV_CONSUMER_KEY = "IBKR_CP_CONSUMER_KEY"
ENV_ACCESS_TOKEN = "IBKR_CP_ACCESS_TOKEN"
ENV_ACCESS_TOKEN_SECRET = "IBKR_CP_ACCESS_TOKEN_SECRET"
ENV_SIGNING_KEY_PEM = "IBKR_CP_SIGNING_KEY_PEM"
ENV_ENCRYPTION_KEY_PEM = "IBKR_CP_ENCRYPTION_KEY_PEM"
ENV_DH_PRIME = "IBKR_CP_DH_PRIME"
ENV_DH_GENERATOR = "IBKR_CP_DH_GENERATOR"
ENV_REALM = "IBKR_CP_REALM"

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
    return bool(env.get(name, "").strip())


def credentials_present(env: Mapping[str, str] | None = None) -> bool:
    resolved = os.environ if env is None else env
    return all(_present(resolved, name) for name in _REQUIRED_ENV)


def _read_pem(env: Mapping[str, str], name: str) -> str:
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
    client = httpx.Client(timeout=timeout, verify=verify_tls)
    root = base_url.rstrip("/")

    def post(path: str, headers: Mapping[str, str]) -> Mapping[str, object]:
        url = f"{root}/{path.lstrip('/')}"
        response = client.post(url, headers=dict(headers))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise CpOAuthError(f"OAuth exchange POST {path!r} returned non-object: {payload!r}")
        return payload

    return post
