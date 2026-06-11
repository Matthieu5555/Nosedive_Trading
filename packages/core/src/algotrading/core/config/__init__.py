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

from .loader import ConfigError, config_from_mapping, from_config, load_platform_config
from .platform_config import (
    DELTA_CONVENTIONS,
    GAMMA_NORMALISATIONS,
    SECTION_NAMES,
    THETA_DAY_COUNTS,
    AnomalyQcConfig,
    ConfigFieldError,
    ContinuityQcConfig,
    FitToleranceQcConfig,
    ForwardConfig,
    ForwardEngineQcConfig,
    GridQcConfig,
    MonetizationConfig,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StressSurfaceConfig,
    StrikeSelectionConfig,
    SurfaceConfig,
    UniverseConfig,
    canonical_json,
    composite_config_hash,
    config_hash,
    config_hashes,
    config_snapshot,
    section_hash,
    section_versions,
)
from .yaml_config import LoadedConfig, load_yaml_config, mapping_config_hash

__all__ = [
    "DELTA_CONVENTIONS",
    "GAMMA_NORMALISATIONS",
    "SECTION_NAMES",
    "THETA_DAY_COUNTS",
    "AnomalyQcConfig",
    "ConfigError",
    "ConfigFieldError",
    "ContinuityQcConfig",
    "FitToleranceQcConfig",
    "ForwardConfig",
    "ForwardEngineQcConfig",
    "GridQcConfig",
    "LoadedConfig",
    "MonetizationConfig",
    "PlatformConfig",
    "QcThresholdConfig",
    "ScenarioConfig",
    "SolverConfig",
    "StressSurfaceConfig",
    "StrikeSelectionConfig",
    "SurfaceConfig",
    "UniverseConfig",
    "canonical_json",
    "composite_config_hash",
    "config_from_mapping",
    "config_hash",
    "config_hashes",
    "config_snapshot",
    "from_config",
    "load_platform_config",
    "load_yaml_config",
    "mapping_config_hash",
    "section_hash",
    "section_versions",
]

# `build_dataclass` retired: the pydantic v2 section models are the validation seam now
# (REP6 / ADR 0028). `ConfigFieldError` moved to `platform_config` with the error boundary.
