"""Load the typed index registry from config — the single seam 1A/1C/1G/1I consume.

The registry block is authored in ``configs/universe.yaml`` and carried, unvalidated, on
:class:`~algotrading.core.config.UniverseConfig.indices` (core stays blind to the calendar
library). This module is the infra-side seam that turns that raw block into the validated,
typed :class:`~algotrading.infra.universe.index_registry.IndexRegistry` and exposes the one
accessor downstream tasks read — :func:`enabled_indices` — so nothing re-parses the YAML.
"""

from __future__ import annotations

from pathlib import Path

from algotrading.core.config import PlatformConfig
from algotrading.core.config.loader import load_platform_config

from .index_registry import IndexEntry, IndexRegistry, parse_index_registry


def index_registry_from_config(config: PlatformConfig) -> IndexRegistry:
    """Parse and validate the registry from an already-loaded :class:`PlatformConfig`.

    Reads ``config.universe.indices`` (the raw hashed block) and runs the full typed
    validation — including the load-bearing unknown-calendar-code rejection — returning the
    frozen :class:`IndexRegistry`. Use this when the platform config is already in hand so
    the bundles are not read twice.
    """
    return parse_index_registry(config.universe.indices)


def load_index_registry(configs_dir: str | Path) -> IndexRegistry:
    """Load + validate the registry straight from the Part VII bundle directory.

    A convenience over :func:`index_registry_from_config` for callers that only need the
    registry: it loads the platform config from ``configs_dir`` and parses the block.
    """
    return index_registry_from_config(load_platform_config(configs_dir))


def enabled_indices(registry: IndexRegistry) -> tuple[IndexEntry, ...]:
    """The enabled registry entries — the single accessor 1A/1C/1G/1I read.

    A thin pass-through to :meth:`IndexRegistry.enabled_indices` so downstream tasks depend
    on one named seam in the universe package rather than reaching into the dataclass. A
    disabled index is absent here and so never reaches capture.
    """
    return registry.enabled_indices()
