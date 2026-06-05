"""Validated, versioned, hashable platform configuration.

Two paths share this package:

* the typed economic config — :class:`PlatformConfig` and its four versioned
  sections, hashed deterministically; this is the blueprint's mandated
  universe/QC/solver/scenario version set. Built from a versioned YAML overlay
  config (``load_yaml_config`` → :func:`from_config`), the single path C7/ADR 0028
  standardize on (the legacy TOML loader was retired).
* the generic versioned-YAML loader with overlay inheritance
  (``load_yaml_config`` → :class:`LoadedConfig`) for free-form config bundles.
"""

from __future__ import annotations

from .loader import ConfigError, config_from_mapping, from_config
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
from .reflective import ConfigFieldError, build_dataclass
from .yaml_config import LoadedConfig, load_yaml_config, mapping_config_hash

__all__ = [
    "SECTION_NAMES",
    "ConfigError",
    "ConfigFieldError",
    "LoadedConfig",
    "PlatformConfig",
    "QcThresholdConfig",
    "ScenarioConfig",
    "SolverConfig",
    "UniverseConfig",
    "build_dataclass",
    "canonical_json",
    "composite_config_hash",
    "config_from_mapping",
    "config_hash",
    "from_config",
    "load_yaml_config",
    "mapping_config_hash",
    "section_hash",
    "section_versions",
]
