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
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .reflective import ConfigFieldError


def _finite(section: str, field: str, value: float) -> None:
    """Reject a non-finite number on an economic field (it would poison the hash)."""
    if not math.isfinite(value):
        raise ConfigFieldError(section, field, value, "must be finite")


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    """Which instruments the platform tracks."""

    version: str
    underlyings: tuple[str, ...]
    exchange: str

    def __post_init__(self) -> None:
        if not self.version:
            raise ConfigFieldError("universe", "version", self.version, "must be non-empty")
        if not self.underlyings:
            raise ConfigFieldError("universe", "underlyings", self.underlyings, "must be non-empty")
        if not self.exchange:
            raise ConfigFieldError("universe", "exchange", self.exchange, "must be non-empty")


@dataclass(frozen=True, slots=True)
class QcThresholdConfig:
    """Cut-offs that decide whether a quote or chain is usable."""

    version: str
    max_spread_pct: float
    max_quote_age_seconds: float
    min_chain_count: int

    def __post_init__(self) -> None:
        _finite("qc_threshold", "max_spread_pct", self.max_spread_pct)
        _finite("qc_threshold", "max_quote_age_seconds", self.max_quote_age_seconds)
        if self.max_spread_pct <= 0.0:
            raise ConfigFieldError(
                "qc_threshold", "max_spread_pct", self.max_spread_pct, "must be > 0"
            )
        if self.max_quote_age_seconds <= 0.0:
            raise ConfigFieldError(
                "qc_threshold", "max_quote_age_seconds", self.max_quote_age_seconds, "must be > 0"
            )
        if self.min_chain_count < 1:
            raise ConfigFieldError(
                "qc_threshold", "min_chain_count", self.min_chain_count, "must be >= 1"
            )


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """How the implied-volatility inversion is run."""

    version: str
    iv_tolerance: float
    max_iterations: int

    def __post_init__(self) -> None:
        _finite("solver", "iv_tolerance", self.iv_tolerance)
        if self.iv_tolerance <= 0.0:
            raise ConfigFieldError("solver", "iv_tolerance", self.iv_tolerance, "must be > 0")
        if self.max_iterations < 1:
            raise ConfigFieldError("solver", "max_iterations", self.max_iterations, "must be >= 1")


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    """The stress grid applied by the risk engine.

    ``roll_down_days`` carries the (default-bearing) construction parameter for the
    time-roll family of the grid. The dataclass default is for in-memory/test
    construction only: the YAML loader (:func:`build_dataclass`) still requires the
    field to be present in ``scenarios.yaml``, so an economic field is never silently
    defaulted on the load path.
    """

    version: str
    spot_shocks: tuple[float, ...]
    vol_shocks: tuple[float, ...]
    roll_down_days: tuple[int, ...] = (1,)

    def __post_init__(self) -> None:
        # Empty shock tuples are valid — a grid with no spot/vol shocks is just the
        # time-roll scenario. Only the shock *values* are constrained: they must be finite.
        for shock in (*self.spot_shocks, *self.vol_shocks):
            _finite("scenario", "shock", shock)
        for days in self.roll_down_days:
            if days <= 0:
                raise ConfigFieldError(
                    "scenario", "roll_down_days", days, "must be a positive day count"
                )


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
    if isinstance(value, float):
        # Collapse -0.0 onto 0.0 so the two never split a reproducibility hash (they are
        # mathematically equal but serialize to different JSON). Non-finite floats fall
        # through and are rejected by canonical_json's allow_nan=False.
        return 0.0 if value == 0.0 else value
    return value


def canonical_json(value: Any) -> str:
    """Return the canonical JSON string for any config object or section.

    ``allow_nan=False`` so a NaN/Inf in a config raises rather than emitting invalid
    ``NaN``/``Infinity`` JSON tokens — a reproducibility hash must never be ill-formed.
    """
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


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
