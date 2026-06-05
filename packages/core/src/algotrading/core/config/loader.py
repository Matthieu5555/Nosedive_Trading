"""Build a :class:`PlatformConfig` from a versioned YAML overlay config or a mapping.

The loader is the only place that knows the on-disk shape of the typed economic
config. It turns lists into the tuples the frozen dataclasses expect and raises a
clear error naming the missing section rather than a raw ``KeyError`` from deep inside.

The economic config is authored in versioned YAML and resolved through the overlay
loader (``from_config`` over a :class:`LoadedConfig` from ``load_yaml_config`` — base +
one overlay, deep-merged), then validated by ``config_from_mapping``. This is the single
path C7/ADR 0028 standardize on; the legacy TOML loader was retired here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .platform_config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from .reflective import build_dataclass
from .yaml_config import LoadedConfig


class ConfigError(Exception):
    """The config file or mapping was missing a required section or field."""


def config_from_mapping(data: Mapping[str, Any]) -> PlatformConfig:
    """Build a validated config from a plain mapping (e.g. resolved YAML).

    Each of the four economic sections is built by the one reflective
    :func:`build_dataclass` seam (coerce by declared type, reject unknown/missing keys,
    validate in ``__post_init__``), so the YAML↔dataclass schema cannot drift and a bad
    field raises a labelled :class:`ConfigFieldError` naming the section and field.
    """
    sections = {
        "universe": UniverseConfig,
        "qc_threshold": QcThresholdConfig,
        "solver": SolverConfig,
        "scenario": ScenarioConfig,
    }
    built: dict[str, Any] = {}
    for name, cls in sections.items():
        if name not in data:
            raise ConfigError(f"config is missing required section '{name}'")
        built[name] = build_dataclass(cls, data[name], section=name)
    return PlatformConfig(**built)


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
