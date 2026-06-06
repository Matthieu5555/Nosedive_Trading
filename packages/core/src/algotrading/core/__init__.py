"""Shared foundation (level 0): cross-cutting, domain-agnostic primitives.

Config loading + hashing, structured logging, the run manifest, and the provenance
stamp — usable by every layer (infra, strategy, execution, frontend) without pulling
any domain dependency. This layer depends on nothing above it.
"""

from __future__ import annotations

from .config import (
    LoadedConfig,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
    composite_config_hash,
    config_hash,
    from_config,
    load_platform_config,
    load_yaml_config,
    section_hash,
    section_versions,
)
from .log import get_logger
from .manifest import Manifest
from .provenance import (
    ProvenanceError,
    ProvenanceStamp,
    ProvenanceValidationError,
    SourceRecordRef,
    canonical_primary_key,
    code_version,
    source_ref,
    stamp,
    validate_stamp,
)

__all__ = [
    "LoadedConfig",
    "Manifest",
    "PlatformConfig",
    "ProvenanceError",
    "ProvenanceStamp",
    "ProvenanceValidationError",
    "QcThresholdConfig",
    "ScenarioConfig",
    "SolverConfig",
    "SourceRecordRef",
    "UniverseConfig",
    "canonical_primary_key",
    "code_version",
    "composite_config_hash",
    "config_hash",
    "get_logger",
    "from_config",
    "load_platform_config",
    "load_yaml_config",
    "section_hash",
    "section_versions",
    "source_ref",
    "stamp",
    "validate_stamp",
]
