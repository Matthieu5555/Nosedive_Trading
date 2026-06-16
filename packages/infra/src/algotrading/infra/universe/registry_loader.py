from __future__ import annotations

from pathlib import Path

from algotrading.core.config import PlatformConfig
from algotrading.core.config.loader import load_platform_config

from .index_registry import IndexEntry, IndexRegistry, parse_index_registry


def index_registry_from_config(config: PlatformConfig) -> IndexRegistry:
    return parse_index_registry(config.universe.indices)


def load_index_registry(configs_dir: str | Path) -> IndexRegistry:
    return index_registry_from_config(load_platform_config(configs_dir))


def enabled_indices(registry: IndexRegistry) -> tuple[IndexEntry, ...]:
    return registry.enabled_indices()
