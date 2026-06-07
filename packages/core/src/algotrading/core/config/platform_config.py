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
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .reflective import ConfigFieldError


def _finite(section: str, field: str, value: float) -> None:
    """Reject a non-finite number on an economic field (it would poison the hash)."""
    if not math.isfinite(value):
        raise ConfigFieldError(section, field, value, "must be finite")


DELTA_CONVENTIONS = ("forward_undiscounted", "spot_discounted")


@dataclass(frozen=True, slots=True)
class StrikeSelectionConfig:
    """The delta-band strike-selection policy parameters (WS 1B, ADR 0028).

    The %-of-spot strike window lives in code (``ChainSelection`` defaults — it is a
    request-shaping heuristic, not an economic number that changes which records exist
    in a model-bearing way). The **delta band**, by contrast, is economic: the 30Δ bound
    decides which strikes land in the captured chain and so which records exist, so it is
    hashed config, never a ``.py`` literal (ADR 0028 / C7 audited site).

    - ``delta_bound`` — the absolute (unsigned) delta cut, ``0.30`` for the 30Δ band. A
      call is kept while its delta ≤ ``+delta_bound`` (down to ATM), a put while its delta
      ≥ ``−delta_bound`` (up to ATM); the kept set is the contiguous block from the 30Δ
      put through ATM to the 30Δ call. Comparisons are on the absolute value.
    - ``delta_convention`` — *which* delta the bound is measured in, pinned so the choice
      is auditable (the gotcha the spec calls out). Built at ``carry == 0`` via
      ``from_forward(spot=None)`` so spot- and forward-delta coincide; the flag then
      selects whether the bound is read against the **undiscounted** forward delta
      (``N(d1)`` for a call) or the **discounted** spot delta the engine returns
      (``discount_factor · N(d1)``). They differ only by the discount factor, which is why
      pinning the flag matters at the boundary.
    - ``min_strikes_per_side`` — the per-tenor floor: keep at least this many of the
      nearest-the-money strikes below and above the forward even when the listing is so
      thin (or the band so tight) that fewer fall inside the delta window, so a sparse
      tenor still yields a fittable slice rather than an empty silent result.
    """

    version: str
    delta_bound: float = 0.30
    delta_convention: str = "forward_undiscounted"
    min_strikes_per_side: int = 2

    def __post_init__(self) -> None:
        if not self.version:
            raise ConfigFieldError(
                "strike_selection", "version", self.version, "must be non-empty"
            )
        _finite("strike_selection", "delta_bound", self.delta_bound)
        if not 0.0 < self.delta_bound < 1.0:
            raise ConfigFieldError(
                "strike_selection",
                "delta_bound",
                self.delta_bound,
                "must lie in (0, 1) — a call delta is in [0, 1]",
            )
        if self.delta_convention not in DELTA_CONVENTIONS:
            raise ConfigFieldError(
                "strike_selection",
                "delta_convention",
                self.delta_convention,
                f"must be one of {DELTA_CONVENTIONS}",
            )
        if self.min_strikes_per_side < 1:
            raise ConfigFieldError(
                "strike_selection",
                "min_strikes_per_side",
                self.min_strikes_per_side,
                "must be >= 1",
            )


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    """Which instruments the platform tracks, and the tenor grid analytics project to.

    ``tenor_grid`` is the ordered set of standard maturities the surface/Greeks are
    projected onto (P0.1 / OQ-4): ``10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y``. It is an
    economic selection-grid parameter (it changes which records exist), so it lives in
    the hashed ``universe`` bundle beside ``ChainSelection`` and enters
    ``config_hashes["universe"]``. The blueprint Part IX data dictionary is the
    authoritative copy (ADR 0011); this YAML copy must equal it as an ordered list, a
    drift a test guards. The order is preserved (a tuple, not a set) because the grid is
    quoted in tenor order downstream.
    """

    version: str
    underlyings: tuple[str, ...]
    exchange: str
    # The dataclass default is for in-memory/test construction only: the YAML loader
    # (build_dataclass) still requires the field present in universe.yaml, so the grid is
    # never silently defaulted on the load path (the same discipline ScenarioConfig uses).
    tenor_grid: tuple[str, ...] = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")
    # The index registry block (ADR 0035) — which indices the platform tracks, keyed by
    # symbol. Held here as the raw nested mapping (canonicalized to a stable, JSON-ready
    # form), NOT as the validated typed `IndexRegistry`: the typed parse + calendar-code
    # validation lives in the infra layer (it needs the `exchange_calendars` library, which
    # core must stay blind to). Keeping the block on `UniverseConfig` is what folds it into
    # `config_hashes["universe"]` (ADR 0035 §4) with no separate hash. The default is empty
    # — an absent `indices:` block is a valid empty registry. The loader special-cases this
    # field (the reflective builder cannot coerce a nested map of maps); see
    # `loader.config_from_mapping`.
    indices: Mapping[str, Any] = field(default_factory=dict)
    # The delta-band strike-selection policy (WS 1B, ADR 0028). The delta bound is an
    # economic field — it decides which strikes the captured chain holds — so it lives in
    # the hashed `universe` bundle and is built through the reflective `from_config` seam
    # (the loader special-cases the `strike_selection:` sub-block, like `indices:`). The
    # dataclass default is for in-memory/test construction; the YAML loader requires the
    # block present so the economic bound is never silently defaulted on the load path.
    strike_selection: StrikeSelectionConfig = field(
        default_factory=lambda: StrikeSelectionConfig(version="strike-selection-default")
    )

    def __post_init__(self) -> None:
        if not self.version:
            raise ConfigFieldError("universe", "version", self.version, "must be non-empty")
        if not self.underlyings:
            raise ConfigFieldError("universe", "underlyings", self.underlyings, "must be non-empty")
        if not self.exchange:
            raise ConfigFieldError("universe", "exchange", self.exchange, "must be non-empty")
        if not self.tenor_grid:
            raise ConfigFieldError(
                "universe", "tenor_grid", self.tenor_grid, "must be non-empty"
            )
        if len(set(self.tenor_grid)) != len(self.tenor_grid):
            raise ConfigFieldError(
                "universe", "tenor_grid", self.tenor_grid, "tenors must be unique"
            )


@dataclass(frozen=True, slots=True)
class GridQcConfig:
    """The grid-aware QC cut-offs: per-tenor coverage floors and the Δ-band window (WS 1H).

    The Phase-1 QC plane validates the projected (tenor × delta-band) grid *as a grid*,
    not as a flat chain (WS 1F / 1H). Two cut-offs are needed and both are economic in the
    ADR-0028 sense — they decide whether a day's grid is judged complete, so they must be
    hashed config, never ``.py`` literals:

    - ``tenor_floors`` — a ``tenor_label → minimum usable-point count`` mapping, keyed on
      the P0.1 pinned tenor grid (``10d…3y``). A pinned tenor whose grid points fall below
      its floor is a coverage breach; a tenor absent from the grid entirely is a breach, not
      a skip. A pinned tenor with **no** configured floor is a config error (the consumer
      raises rather than defaulting to zero) — the floor must be a deliberate per-tenor
      choice, never silently absent.
    - ``band_low_delta`` / ``band_high_delta`` — the signed delta band the selected strikes
      must span (``-0.30`` → ``+0.30``, i.e. the 30Δ put → ATM → 30Δ call window, WS 1B).
      The edges come from config, never from the data under test, so a thin chain *fails*
      rather than silently defining its own band.
    - ``max_delta_step`` — the largest acceptable gap between consecutive selected deltas
      inside the band; a hole wider than this is a completeness breach.

    Held as a nested block on :class:`QcThresholdConfig` so it folds into
    ``config_hashes["qc"]`` with no separate hash. ``tenor_floors`` is a mapping the flat
    reflective coercion cannot type, so the loader builds it specially (see
    ``loader._build_qc_threshold``); the dataclass default is for in-memory/test
    construction only.
    """

    version: str
    tenor_floors: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType(
            {
                "10d": 5,
                "1m": 5,
                "3m": 5,
                "6m": 5,
                "12m": 5,
                "18m": 5,
                "2y": 5,
                "3y": 5,
            }
        )
    )
    band_low_delta: float = -0.30
    band_high_delta: float = 0.30
    max_delta_step: float = 0.25

    def __post_init__(self) -> None:
        if not self.version:
            raise ConfigFieldError("grid_qc", "version", self.version, "must be non-empty")
        _finite("grid_qc", "band_low_delta", self.band_low_delta)
        _finite("grid_qc", "band_high_delta", self.band_high_delta)
        _finite("grid_qc", "max_delta_step", self.max_delta_step)
        if not (-1.0 <= self.band_low_delta < self.band_high_delta <= 1.0):
            raise ConfigFieldError(
                "grid_qc",
                "band_low_delta",
                (self.band_low_delta, self.band_high_delta),
                "require -1 <= band_low_delta < band_high_delta <= 1",
            )
        if self.max_delta_step <= 0.0:
            raise ConfigFieldError(
                "grid_qc", "max_delta_step", self.max_delta_step, "must be > 0"
            )
        for tenor, floor in self.tenor_floors.items():
            if not isinstance(tenor, str) or not tenor:
                raise ConfigFieldError(
                    "grid_qc", "tenor_floors", tenor, "tenor label must be a non-empty string"
                )
            if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
                raise ConfigFieldError(
                    "grid_qc", "tenor_floors", {tenor: floor}, "floor must be an int >= 0"
                )
        # Freeze the mapping so the config stays deeply immutable and hashable (a plain dict
        # passed by a caller would otherwise be mutable on a frozen dataclass).
        if not isinstance(self.tenor_floors, MappingProxyType):
            object.__setattr__(
                self, "tenor_floors", MappingProxyType(dict(self.tenor_floors))
            )

    def floor_for(self, tenor: str) -> int:
        """The configured floor for ``tenor``, or raise if the pinned tenor has none.

        A pinned tenor with no configured floor is a config error — the coverage-floor
        check must never default a missing floor to zero (a tenor would then pass for free).
        """
        if tenor not in self.tenor_floors:
            raise ConfigFieldError(
                "grid_qc", "tenor_floors", tenor, "no coverage floor configured for pinned tenor"
            )
        return self.tenor_floors[tenor]


@dataclass(frozen=True, slots=True)
class QcThresholdConfig:
    """Cut-offs that decide whether a quote or chain is usable.

    The flat scalar cut-offs (``max_spread_pct``…``min_chain_count``) gate the
    instrument-agnostic quote/chain checks. The nested ``grid`` block (WS 1H) carries the
    grid-aware cut-offs — the per-tenor coverage floors and the Δ-band window — and folds
    into the same ``qc`` config hash. The dataclass default for ``grid`` is for in-memory
    /test construction; the loader requires the ``grid:`` block present in ``qc.yaml`` so
    the economic floors are never silently defaulted on the load path.
    """

    version: str
    max_spread_pct: float
    max_quote_age_seconds: float
    min_chain_count: int
    grid: GridQcConfig = field(
        default_factory=lambda: GridQcConfig(version="grid-qc-default")
    )

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


GAMMA_NORMALISATIONS = ("one_pct", "one_dollar")
THETA_DAY_COUNTS = (365, 252)


@dataclass(frozen=True, slots=True)
class MonetizationConfig:
    """The two genuine $-Greek convention forks, as explicit flags (P0.2 / OQ-1, ADR 0036).

    ``gamma_normalisation`` picks whether Gamma\\$ is quoted per **1% move**
    (``one_pct`` → Γ·S²/100, the default and pinned unit) or per **\\$1 move**
    (``one_dollar`` → Γ·S²). ``theta_day_count`` picks the calendar Theta\\$ divisor —
    **365** (per calendar day, the default and pinned unit) or **252** (per trading day).
    Both are economic: they change the dollar numbers stored, so they live in the hashed
    ``scenarios`` bundle (the risk-layer params, ADR 0028) and enter
    ``config_hashes["scenarios"]``. The defaults match the units the data dictionary and
    ADR 0036 pin (gamma per 1%, theta ÷365).
    """

    version: str
    gamma_normalisation: str = "one_pct"
    theta_day_count: int = 365

    def __post_init__(self) -> None:
        if not self.version:
            raise ConfigFieldError("monetization", "version", self.version, "must be non-empty")
        if self.gamma_normalisation not in GAMMA_NORMALISATIONS:
            raise ConfigFieldError(
                "monetization",
                "gamma_normalisation",
                self.gamma_normalisation,
                f"must be one of {GAMMA_NORMALISATIONS}",
            )
        if self.theta_day_count not in THETA_DAY_COUNTS:
            raise ConfigFieldError(
                "monetization",
                "theta_day_count",
                self.theta_day_count,
                f"must be one of {THETA_DAY_COUNTS}",
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
    # Default for in-memory/test construction; the YAML loader requires the
    # `monetization:` block present in scenarios.yaml (it is an economic input).
    monetization: MonetizationConfig = field(
        default_factory=lambda: MonetizationConfig(version="monetization-default")
    )


# The section names, in the order they hash, exposed so callers (and tests)
# can iterate the version stamps without hand-listing them.
SECTION_NAMES = (
    "universe",
    "qc_threshold",
    "solver",
    "surface",
    "forward",
    "scenario",
    "monetization",
)


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
    "scenarios": ("scenario", "monetization"),
}


def config_snapshot(config: PlatformConfig) -> dict[str, Any]:
    """Return the fully-resolved config as a plain, JSON-ready mapping (the manifest freeze).

    One key per typed section (``{universe, qc_threshold, solver, surface, forward,
    scenario}``), each the section's canonical field mapping. A run's manifest stores this
    snapshot so the run replays from the manifest alone — git is dev-time only (ADR 0028).
    It round-trips through :func:`config_from_mapping`, and ``validate_manifest`` recomputes
    :func:`config_hashes` from it and rejects a snapshot whose hashes do not match.
    """
    return {name: _canonical(getattr(config, name)) for name in SECTION_NAMES}


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
