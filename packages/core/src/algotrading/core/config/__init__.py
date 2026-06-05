"""Validated, versioned, hashable platform configuration.

Two paths share this package:

* the typed economic config — :class:`PlatformConfig` and its four versioned
  sections, loaded from TOML (``load_config``), hashed deterministically; this is
  the blueprint's mandated universe/QC/solver/scenario version set.
* the generic versioned-YAML loader with overlay inheritance
  (``load_yaml_config`` → :class:`LoadedConfig`) for free-form config bundles.
"""

from __future__ import annotations

from .loader import ConfigError, config_from_mapping, load_config
from .platform_config import (
    SECTION_NAMES,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
    canonical_json,
    composite_config_hash,
    config_hash,
    section_hash,
    section_versions,
)
from .yaml_config import LoadedConfig, load_yaml_config, mapping_config_hash

__all__ = [
    "SECTION_NAMES",
    "ConfigError",
    "LoadedConfig",
    "PlatformConfig",
    "QcThresholdConfig",
    "ScenarioConfig",
    "SolverConfig",
    "UniverseConfig",
    "canonical_json",
    "composite_config_hash",
    "config_from_mapping",
    "config_hash",
    "load_config",
    "load_yaml_config",
    "mapping_config_hash",
    "section_hash",
    "section_versions",
]
