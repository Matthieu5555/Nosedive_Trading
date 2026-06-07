"""Shared foundation (level 0): cross-cutting, domain-agnostic primitives.

Config loading + hashing, structured logging, the run manifest, and the provenance
stamp — usable by every layer (infra, strategy, execution, frontend) without pulling
any domain dependency. This layer depends on nothing above it.
"""

from __future__ import annotations

from .config import (
    ForwardConfig,
    LoadedConfig,
    MonetizationConfig,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StressSurfaceConfig,
    SurfaceConfig,
    UniverseConfig,
    composite_config_hash,
    config_hash,
    config_hashes,
    config_snapshot,
    from_config,
    load_platform_config,
    load_yaml_config,
    section_hash,
    section_versions,
)
from .log import get_logger
from .manifest import Manifest, ManifestValidationError, validate_manifest
from .provenance import (
    ProvenanceError,
    ProvenanceStamp,
    ProvenanceValidationError,
    SourceRecordRef,
    canonical_primary_key,
    code_identity,
    code_version,
    source_ref,
    stamp,
    validate_stamp,
)

__all__ = [
    "ForwardConfig",
    "LoadedConfig",
    "Manifest",
    "ManifestValidationError",
    "MonetizationConfig",
    "PlatformConfig",
    "ProvenanceError",
    "ProvenanceStamp",
    "ProvenanceValidationError",
    "QcThresholdConfig",
    "ScenarioConfig",
    "SolverConfig",
    "SourceRecordRef",
    "StressSurfaceConfig",
    "SurfaceConfig",
    "UniverseConfig",
    "canonical_primary_key",
    "code_identity",
    "code_version",
    "composite_config_hash",
    "config_hash",
    "config_hashes",
    "config_snapshot",
    "get_logger",
    "from_config",
    "load_platform_config",
    "load_yaml_config",
    "section_hash",
    "section_versions",
    "source_ref",
    "stamp",
    "validate_manifest",
    "validate_stamp",
]
