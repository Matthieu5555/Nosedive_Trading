"""The validated configuration object and its content hashes.

Every number that affects economics lives here, in one frozen object, instead of
being scattered as literals across modules. The object splits into four sections,
each carrying its own version stamp: universe, qc-threshold, solver, scenario.
The four versions are independent — bumping the solver version says "the solver
changed" without pretending the scenario grid changed too. (The blueprint, Part I
"Core naming conventions", mandates versioning every configuration set: universe
version, QC threshold version, solver version, and scenario-grid version.)

Two hashes are derived from the config and both are deliberately built from
canonical JSON (sorted keys, fixed number formatting) hashed with SHA-256, never
from Python's built-in ``hash()``. ``hash()`` is salted per process, so a
dict/set hashed today and tomorrow differ; SHA-256 of canonical JSON is the same
on every machine, in every run, forever. That stability is what lets a historical
computation be reproduced and checked.

``config_hash`` covers all four sections — change any economic field and it moves.
``section_hash`` covers one section — bump the solver version and only the solver
section's hash moves. ``composite_config_hash`` folds several independent config
hashes into one key, for an output shaped by more than one config bundle. Runtime
or environment settings such as the storage path are deliberately *not* in here:
they are environment, not economics, and must not change the reproducibility hash.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    """Which instruments the platform tracks."""

    version: str
    underlyings: tuple[str, ...]
    exchange: str


@dataclass(frozen=True, slots=True)
class QcThresholdConfig:
    """Cut-offs that decide whether a quote or chain is usable."""

    version: str
    max_spread_pct: float
    max_quote_age_seconds: float
    min_chain_count: int


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """How the implied-volatility inversion is run."""

    version: str
    iv_tolerance: float
    max_iterations: int


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    """The stress grid applied by the risk engine."""

    version: str
    spot_shocks: tuple[float, ...]
    vol_shocks: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PlatformConfig:
    """The whole economic configuration: four versioned sections."""

    universe: UniverseConfig
    qc_threshold: QcThresholdConfig
    solver: SolverConfig
    scenario: ScenarioConfig


# The four section names, in the order they hash, exposed so callers (and tests)
# can iterate the version stamps without hand-listing them.
SECTION_NAMES = ("universe", "qc_threshold", "solver", "scenario")


def _canonical(value: Any) -> Any:
    """Turn a config value into something with one, stable JSON form.

    Tuples and lists become lists; dataclasses become key-sorted dicts; floats
    are left to JSON. The point is that the same logical config always produces
    byte-identical JSON.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Return the canonical JSON string for any config object or section."""
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_hash(config: PlatformConfig) -> str:
    """Hash the whole config. Moves when any economic field in any section moves."""
    return _sha256(canonical_json(config))


def section_hash(config: PlatformConfig, section: str) -> str:
    """Hash one named section. Moves only when that section's fields move.

    Raises ``KeyError`` for an unknown section name rather than guessing, so a
    typo fails loudly instead of silently hashing nothing.
    """
    if section not in SECTION_NAMES:
        raise KeyError(section)
    return _sha256(canonical_json(getattr(config, section)))


def section_versions(config: PlatformConfig) -> dict[str, str]:
    """Return the four independent version stamps keyed by section name."""
    return {name: getattr(config, name).version for name in SECTION_NAMES}


def composite_config_hash(parts: Mapping[str, str]) -> str:
    """Combine per-component config hashes into one deterministic key.

    Reproducibility requires the ``config_hash`` branded onto an output to reflect
    EVERY config input that shaped it, not just one. When a result depends on
    several configs (e.g. qc + a per-broker forward config), hashing the sorted
    ``{component: hash}`` mapping makes the single key change whenever any component
    changes, so two distinct input sets can never collide on the same key. The
    per-component breakdown stays available separately (the manifest) for diagnostics.
    """
    canonical = json.dumps(
        {str(k): str(v) for k, v in parts.items()}, sort_keys=True, separators=(",", ":")
    )
    return _sha256(canonical)
