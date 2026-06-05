"""Saxo environment configuration: which gateway (sim/live) to target.

The canonical default lives in the versioned ``configs/saxo.yaml``. The runtime environment
is resolved with the precedence ``--env`` CLI flag > ``SAXO_ENV`` env var > yaml default, so a
machine can override without editing the versioned file. Secrets (tokens, client id/secret)
are NEVER read here — they stay in ``.env``.

Options market data requires the live environment (sim is Forex-only); ``expected_delay_minutes``
records the free delayed feed's lag so downstream staleness logic ages delayed quotes correctly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from algotrading.core.config import LoadedConfig, load_yaml_config

from .connectivity.saxo_transport import SaxoTransport

# configs/saxo.yaml sits beside src/ in the package: src/algotrading/infra_saxo/config.py
# parents[3] == packages/infra-saxo
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "saxo.yaml"

_VALID_ENVIRONMENTS = ("sim", "live")

# OAuth token endpoints per environment (the SAXO_TOKEN_URL env var overrides for custom setups).
_AUTH_HOSTS = {
    "live": "https://live.logonvalidation.net",
    "sim": "https://sim.logonvalidation.net",
}


@dataclass(frozen=True)
class SaxoConfig:
    """Resolved Saxo environment configuration with provenance."""

    environment: str  # "sim" | "live"
    expected_delay_minutes: int
    config_hash: str

    @classmethod
    def from_config(cls, loaded: LoadedConfig) -> SaxoConfig:
        data = loaded.data
        env = str(data.get("environment", "sim")).lower()
        if env not in _VALID_ENVIRONMENTS:
            raise ValueError(
                f"Invalid Saxo environment {env!r} in config; expected one of {_VALID_ENVIRONMENTS}"
            )
        return cls(
            environment=env,
            expected_delay_minutes=int(data.get("expected_delay_minutes", 15)),
            config_hash=loaded.config_hash,
        )


def load_saxo_config(path: str | Path | None = None) -> SaxoConfig:
    """Load the Saxo environment config from ``configs/saxo.yaml`` (or an explicit path)."""
    return SaxoConfig.from_config(load_yaml_config(Path(path) if path else _DEFAULT_CONFIG_PATH))


def resolve_environment(cli_env: str | None = None, config: SaxoConfig | None = None) -> str:
    """Return the effective environment with precedence ``cli_env`` > ``SAXO_ENV`` > yaml default.

    Raises ``ValueError`` on an unrecognized value from any source.
    """
    env_var = os.getenv("SAXO_ENV")
    chosen = cli_env or env_var or (config or load_saxo_config()).environment
    chosen = chosen.lower()
    if chosen not in _VALID_ENVIRONMENTS:
        raise ValueError(
            f"Invalid Saxo environment {chosen!r}; expected one of {_VALID_ENVIRONMENTS}"
        )
    return chosen


def base_url_for(environment: str) -> str:
    """Map an environment name to the Saxo OpenAPI gateway base URL."""
    return SaxoTransport.LIVE_BASE_URL if environment == "live" else SaxoTransport.SIM_BASE_URL


def token_url_for(environment: str) -> str:
    """Map an environment to the Saxo OAuth token endpoint (SAXO_TOKEN_URL env var overrides)."""
    host = _AUTH_HOSTS.get(environment, _AUTH_HOSTS["live"])
    return os.getenv("SAXO_TOKEN_URL") or f"{host}/token"


def authorize_url_for(environment: str) -> str:
    """Map an environment to the Saxo OAuth authorization endpoint.

    Shares ``_AUTH_HOSTS`` with :func:`token_url_for` so the authorize and token calls always target
    the same gateway for a given environment — a mismatch (authorize on sim, token on live) breaks
    the live options flow.
    """
    host = _AUTH_HOSTS.get(environment, _AUTH_HOSTS["live"])
    return f"{host}/authorize"
