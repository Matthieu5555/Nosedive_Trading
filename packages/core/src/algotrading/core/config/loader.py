"""Build a :class:`PlatformConfig` from a TOML file or an in-memory mapping.

The loader is the only place that knows the on-disk shape of the typed economic
config. It turns lists into the tuples the frozen dataclasses expect and raises a
clear error naming the missing section rather than a raw ``KeyError`` from deep inside.
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


def load_config(path: Path) -> PlatformConfig:
    """Read a TOML config file and return a validated :class:`PlatformConfig`."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return config_from_mapping(data)
