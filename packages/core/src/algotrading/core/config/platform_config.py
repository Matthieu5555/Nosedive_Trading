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
    """How the implied-volatility inversion is run.

    ``vol_min``/``vol_max`` are the search bracket the bracketed solve runs on: a
    near-zero floor standing in for "zero vol", and a ceiling above any real vol beyond
    which a target is treated as unresolvable. They carry dataclass defaults for
    in-memory/test construction, but the YAML loader requires them present, so the
    economic bracket is never silently defaulted on the load path.
    """

    version: str
    iv_tolerance: float
    max_iterations: int
    vol_min: float = 1e-9
    vol_max: float = 5.0

    def __post_init__(self) -> None:
        _finite("solver", "iv_tolerance", self.iv_tolerance)
        _finite("solver", "vol_min", self.vol_min)
        _finite("solver", "vol_max", self.vol_max)
        if self.iv_tolerance <= 0.0:
            raise ConfigFieldError("solver", "iv_tolerance", self.iv_tolerance, "must be > 0")
        if self.max_iterations < 1:
            raise ConfigFieldError("solver", "max_iterations", self.max_iterations, "must be >= 1")
        if not 0.0 < self.vol_min < self.vol_max:
            raise ConfigFieldError(
                "solver",
                "vol_min/vol_max",
                (self.vol_min, self.vol_max),
                "need 0 < vol_min < vol_max",
            )


def _bound_pair(section: str, field: str, value: tuple[float, ...]) -> None:
    """Reject a feasible-range pair that is not a finite, strictly increasing (low, high)."""
    if len(value) != 2:
        raise ConfigFieldError(section, field, value, "must be a (low, high) pair")
    low, high = value
    _finite(section, field, low)
    _finite(section, field, high)
    if not low < high:
        raise ConfigFieldError(section, field, value, "need low < high")


@dataclass(frozen=True, slots=True)
class SurfaceConfig:
    """Bounds and tolerances for the SVI surface fit.

    The five parameter feasible ranges constrain the calibration search and back the
    bound-hit diagnostic; ``svi_bound_hit_tol`` is how close (relative to a range) a
    fitted parameter must sit to count as "at the bound"; ``svi_max_iterations`` caps the
    least-squares budget. Each ``*_bounds`` is a ``(low, high)`` pair. Authored in
    ``pricing.yaml`` under ``surface:``. (The minimum-points floor for SVI is a
    mathematical invariant — five parameters need five points — and stays a code
    constant, not a tunable.)
    """

    version: str
    svi_a_bounds: tuple[float, ...]
    svi_b_bounds: tuple[float, ...]
    svi_rho_bounds: tuple[float, ...]
    svi_m_bounds: tuple[float, ...]
    svi_sigma_bounds: tuple[float, ...]
    svi_bound_hit_tol: float
    svi_max_iterations: int

    def __post_init__(self) -> None:
        if not self.version:
            raise ConfigFieldError("surface", "version", self.version, "must be non-empty")
        _bound_pair("surface", "svi_a_bounds", self.svi_a_bounds)
        _bound_pair("surface", "svi_b_bounds", self.svi_b_bounds)
        _bound_pair("surface", "svi_rho_bounds", self.svi_rho_bounds)
        _bound_pair("surface", "svi_m_bounds", self.svi_m_bounds)
        _bound_pair("surface", "svi_sigma_bounds", self.svi_sigma_bounds)
        _finite("surface", "svi_bound_hit_tol", self.svi_bound_hit_tol)
        if self.svi_bound_hit_tol <= 0.0:
            raise ConfigFieldError(
                "surface", "svi_bound_hit_tol", self.svi_bound_hit_tol, "must be > 0"
            )
        if self.svi_max_iterations < 1:
            raise ConfigFieldError(
                "surface", "svi_max_iterations", self.svi_max_iterations, "must be >= 1"
            )


@dataclass(frozen=True, slots=True)
class ForwardConfig:
    """Confidence/quality heuristics for the put-call-parity forward estimate.

    These map a maturity's used-pair count and relative fit residual to a quality label
    and a 0..1 confidence every downstream consumer trusts. Authored in ``pricing.yaml``
    under ``forward:``. (The minimum-pairs floor for the regression — two unknowns need
    two equations — and the residual float-noise floor are mathematical/precision
    invariants and stay code constants.)
    """

    version: str
    good_rel_residual: float
    fair_rel_residual: float
    full_credit_pairs: float
    rel_residual_halflife: float
    single_pair_confidence: float

    def __post_init__(self) -> None:
        if not self.version:
            raise ConfigFieldError("forward", "version", self.version, "must be non-empty")
        for name in ("good_rel_residual", "fair_rel_residual", "rel_residual_halflife"):
            value = getattr(self, name)
            _finite("forward", name, value)
            if value <= 0.0:
                raise ConfigFieldError("forward", name, value, "must be > 0")
        if self.full_credit_pairs <= 0.0:
            raise ConfigFieldError(
                "forward", "full_credit_pairs", self.full_credit_pairs, "must be > 0"
            )
        if not 0.0 <= self.single_pair_confidence <= 1.0:
            raise ConfigFieldError(
                "forward",
                "single_pair_confidence",
                self.single_pair_confidence,
                "must be in [0, 1]",
            )


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
    """The whole economic configuration: the versioned typed sections."""

    universe: UniverseConfig
    qc_threshold: QcThresholdConfig
    solver: SolverConfig
    surface: SurfaceConfig
    forward: ForwardConfig
    scenario: ScenarioConfig


# The section names, in the order they hash, exposed so callers (and tests)
# can iterate the version stamps without hand-listing them.
SECTION_NAMES = ("universe", "qc_threshold", "solver", "surface", "forward", "scenario")


def _canonical(value: Any) -> Any:
    """Turn a config value into something with one, stable JSON form.

    Tuples and lists become lists; dataclasses and mappings become dicts (their
    values canonicalized too); floats are left to JSON. The point is that the same
    logical config always produces byte-identical JSON.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
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


# Each hashed bundle (a manifest ``config_hashes`` key, named for its Part VII YAML
# file) → the PlatformConfig section attributes authored in that file. ``pricing`` groups
# the solver/surface/forward sections that share ``pricing.yaml``.
_BUNDLE_SECTIONS: dict[str, tuple[str, ...]] = {
    "universe": ("universe",),
    "qc": ("qc_threshold",),
    "pricing": ("solver", "surface", "forward"),
    "scenarios": ("scenario",),
}


def config_hashes(config: PlatformConfig) -> dict[str, str]:
    """Return the per-bundle reproducibility hashes — the blueprint manifest form.

    One SHA-256 over canonical JSON per hashed Part VII bundle
    (``{universe, qc, pricing, scenarios}``), each covering the typed sections authored
    in that bundle's file. This is the canonical key branded onto every derived record's
    :class:`~algotrading.core.provenance.ProvenanceStamp` (ADR 0028): the dict says which
    bundle changed, and a folded ``config_hash`` is at most a derived convenience.
    """
    return {
        bundle: _sha256(canonical_json({name: getattr(config, name) for name in names}))
        for bundle, names in _BUNDLE_SECTIONS.items()
    }


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
