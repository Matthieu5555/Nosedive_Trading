"""Build a :class:`PlatformConfig` from a YAML overlay, a TOML file, or a mapping.

The loader is the only place that knows the on-disk shape of the typed economic
config. It turns lists into the tuples the frozen dataclasses expect and raises a
clear error naming the missing section rather than a raw ``KeyError`` from deep inside.

Two source forms reach the same validation (``config_from_mapping``): the versioned
YAML overlay path (``from_config`` over a :class:`LoadedConfig` resolved by
``load_yaml_config`` — base + one overlay, deep-merged), and the legacy TOML path
(``load_config``). The YAML overlay path is the one C7/ADR 0028 standardize on; the
TOML path is kept until its remaining consumers migrate.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .platform_config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from .yaml_config import LoadedConfig


class ConfigError(Exception):
    """The config file or mapping was missing a required section or field."""


def config_from_mapping(data: dict[str, Any]) -> PlatformConfig:
    """Build a validated config from a plain mapping (e.g. parsed TOML)."""
    try:
        universe = data["universe"]
        qc = data["qc_threshold"]
        solver = data["solver"]
        scenario = data["scenario"]
    except KeyError as missing:
        raise ConfigError(f"config is missing required section {missing}") from missing

    return PlatformConfig(
        universe=UniverseConfig(
            version=universe["version"],
            underlyings=tuple(universe["underlyings"]),
            exchange=universe["exchange"],
        ),
        qc_threshold=QcThresholdConfig(
            version=qc["version"],
            max_spread_pct=float(qc["max_spread_pct"]),
            max_quote_age_seconds=float(qc["max_quote_age_seconds"]),
            min_chain_count=int(qc["min_chain_count"]),
        ),
        solver=SolverConfig(
            version=solver["version"],
            iv_tolerance=float(solver["iv_tolerance"]),
            max_iterations=int(solver["max_iterations"]),
        ),
        scenario=ScenarioConfig(
            version=scenario["version"],
            spot_shocks=tuple(float(x) for x in scenario["spot_shocks"]),
            vol_shocks=tuple(float(x) for x in scenario["vol_shocks"]),
        ),
    )


def from_config(loaded: LoadedConfig) -> PlatformConfig:
    """Build a validated :class:`PlatformConfig` from a resolved YAML overlay config.

    The economic config is authored in versioned YAML and resolved through the overlay
    loader (``load_yaml_config`` — base + one overlay, deep-merged), then validated into
    the frozen dataclasses by the *same* ``config_from_mapping`` the TOML path uses. This
    is the unified typed entry C7/ADR 0028 standardize on: one schema, one validation, the
    overlay loader's inheritance instead of a second untyped path.

    The four required sections (``universe``, ``qc_threshold``, ``solver``, ``scenario``)
    must be present in the resolved mapping; a missing one raises :class:`ConfigError`.
    """
    return config_from_mapping(dict(loaded.data))


def load_config(path: Path) -> PlatformConfig:
    """Read a TOML config file and return a validated :class:`PlatformConfig`.

    Legacy path: prefer authoring the economic config in YAML and loading it through
    ``load_yaml_config`` + :func:`from_config`. Kept until the remaining TOML consumers
    migrate (C7 task 1's retirement step).
    """
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return config_from_mapping(data)
