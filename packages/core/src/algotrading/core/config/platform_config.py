"""The validated configuration object and its content hashes.

Every number that affects economics lives here, in one frozen object, instead of
being scattered as literals across modules. The object splits into four sections,
each carrying its own version stamp: universe, qc-threshold, solver, scenario.
The four versions are independent — bumping the solver version says "the solver
changed" without pretending the scenario grid changed too. (The blueprint, Part I
"Core naming conventions", mandates versioning every configuration set: universe
version, QC threshold version, solver version, and scenario-grid version.)

The sections are pydantic v2 models — **frozen** (immutable, hashable),
``extra="forbid"`` (an unknown YAML key is rejected, never ignored) and
``strict=True`` (``10.5`` for an ``int`` field is a config error, not a silent
truncation; a ``bool`` is not an ``int``). The range/membership checks ride on
``Field(gt/ge/lt/le)`` and ``Literal[...]`` constraints, so the schema *is* the
validation — there is no hand-rolled coercion engine to drift from it.

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
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal, NoReturn

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from ..hashing import canonical_dumps, sha256_hex


class ConfigFieldError(Exception):
    """A config field was missing, unknown, or out of range.

    Carries the ``section`` (the config bundle/section name), the offending ``field``, the
    ``value`` seen, and a plain-language ``reason``, so a bad config names exactly what was
    wrong instead of failing deep inside with a bare ``KeyError``/``ValueError``. Both
    boundaries map a pydantic ``ValidationError`` onto this structured form: the section
    models' shared ``_ConfigModel`` base on direct construction (``section`` = the model
    class name) and the loader on a whole-section validate (``section`` = the bundle key) —
    ADR 0028: a config failure raises a labelled ``ConfigFieldError``, never a silent default.
    """

    def __init__(self, section: str, field: str, value: Any, reason: str = "") -> None:
        self.section = section
        self.field = field
        self.value = value
        self.reason = reason
        suffix = f": {reason}" if reason else ""
        super().__init__(f"config {section}.{field} = {value!r} is invalid{suffix}")


# Shared model config for every economic section: deeply immutable + hashable
# (``frozen``), no unknown YAML keys (``extra="forbid"``), and no lossy coercion
# (``strict``) so ``10.5 → int`` and ``bool → int`` are rejected, not truncated.
_SECTION_CONFIG = ConfigDict(frozen=True, extra="forbid", strict=True)


def _raise_config_field_error(section: str, exc: ValidationError) -> NoReturn:
    """Map a pydantic ``ValidationError`` onto the structured :class:`ConfigFieldError`.

    Takes the first reported error, joins its location path into a dotted ``field`` (so a
    nested ``grid.tenor_floors.10d`` is named in full), and carries the offending ``input``
    value and pydantic's message as ``reason`` — preserving the section/field semantics
    callers and tests depend on (ADR 0028) instead of leaking pydantic's error type.
    """
    error = exc.errors()[0]
    location = error.get("loc", ())
    field = ".".join(str(part) for part in location) if location else "<root>"
    raise ConfigFieldError(section, field, error.get("input"), error.get("msg", "")) from exc


class _ConfigModel(BaseModel):
    """Base for every economic section: frozen + strict + extra-forbid, with the one
    error boundary that re-raises a pydantic ``ValidationError`` as a labelled
    :class:`ConfigFieldError` (ADR 0028 / REP6 6c). Direct construction and the YAML
    loader both flow through here, so a bad field names its section/field the same way
    everywhere — the section name is the model class name (``UniverseConfig``…), which
    the loader overwrites with the bundle key when it re-validates a whole section.
    """

    model_config = _SECTION_CONFIG

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            _raise_config_field_error(type(self).__name__, exc)


def _list_to_tuple(value: Any) -> Any:
    """Turn a YAML list into the tuple a frozen tuple-field expects (a no-op otherwise).

    YAML sequences parse to lists, but the economic grids are declared as ``tuple[...]``
    so the section stays hashable/immutable. Under ``strict=True`` a list is *not* a tuple,
    so this before-validator bridges the on-disk list to the in-memory tuple; element-type
    checking (and the strict ``float``/``int`` rules) still run afterwards.
    """
    if isinstance(value, list):
        return tuple(value)
    return value


# A YAML list of values → an immutable tuple, with strict per-element typing preserved.
_FloatTuple = Annotated[tuple[float, ...], BeforeValidator(_list_to_tuple)]
_IntTuple = Annotated[tuple[int, ...], BeforeValidator(_list_to_tuple)]
_StrTuple = Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)]
# A (low, high) float pair: exactly two finite floats, strictly increasing (checked below).
_FloatPair = Annotated[tuple[float, ...], BeforeValidator(_list_to_tuple)]


DELTA_CONVENTIONS = ("forward_undiscounted", "spot_discounted")


class StrikeSelectionConfig(_ConfigModel):
    """The delta-band strike-selection policy parameters (WS 1B, ADR 0028).

    The %-of-spot strike window lives in code (``ChainSelection`` defaults — it is a
    request-shaping heuristic, not an economic number that changes which records exist
    in a model-bearing way). The **delta band**, by contrast, is economic: the 30Δ bound
    decides which strikes land in the captured chain and so which records exist, so it is
    hashed config, never a ``.py`` literal (ADR 0028 / C7 audited site).

    - ``delta_bound`` — the absolute (unsigned) delta cut, ``0.30`` for the 30Δ band. A
      call is kept while its delta ≤ ``+delta_bound`` (down to ATM), a put while its delta
      ≥ ``−delta_bound`` (up to ATM); the kept set is the contiguous block from the 30Δ
      put through ATM to the 30Δ call. Comparisons are on the absolute value. Must lie in
      ``(0, 1)`` — a call delta is in ``[0, 1]``.
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
    - ``discovery_working_vol`` — the conservative per-index working volatility that *sizes
      the discovery strike window* (T-delta-window). The fitted vol that the economic 30Δ
      band reads is only known downstream, so discovery cannot use it; it qualifies a
      delta-driven, tenor-aware **superset** of the band using this seed vol (the band edges
      grow with ``√T``), wide enough that the downstream :func:`select_strikes_delta_band`
      can always reach the true 30Δ put and call instead of being clipped to ~ATM±1% by a
      fixed strike count. It is a request-shaping *sizing* seed, **not** an economic band
      selector — the band is chosen downstream with the fitted vol, never this value — so it
      is deliberately set conservative-HIGH (over-qualify, never under: under-sizing it
      re-creates the clip the task killed). It lives here as auditable hashed config rather
      than a ``.py`` literal (ADR 0028 / C7), and because under-sizing it *can* change which
      strikes are captured it is reproducibility-relevant and folds into the universe hash.
      One value, not per-index: adding an index stays "conid + enabled" with nothing to
      hand-tune; if a genuinely more volatile index ever clips, ``delta_band_completeness``
      QC turns red and an override is added then, data-driven, never speculatively.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    delta_bound: float = Field(default=0.30, gt=0.0, lt=1.0)
    delta_convention: Literal["forward_undiscounted", "spot_discounted"] = "forward_undiscounted"
    min_strikes_per_side: int = Field(default=2, ge=1)
    discovery_working_vol: float = Field(default=0.40, gt=0.0)


class UniverseConfig(_ConfigModel):
    """Which instruments the platform tracks, and the tenor grid analytics project to.

    ``tenor_grid`` is the ordered set of standard maturities the surface/Greeks are
    projected onto (P0.1 / OQ-4): ``10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y``. It is an
    economic selection-grid parameter (it changes which records exist), so it lives in
    the hashed ``universe`` bundle beside ``ChainSelection`` and enters
    ``config_hashes["universe"]``. The blueprint Part IX data dictionary is the
    authoritative copy (ADR 0011); this YAML copy must equal it as an ordered list, a
    drift a test guards. The order is preserved (a tuple, not a set) because the grid is
    quoted in tenor order downstream — and the grid must hold no duplicate tenor.

    ``dispersion_top_n`` is the S1 dispersion-basket size: how many of an index's heaviest
    constituents by index weight the top-N resolver
    (:func:`~algotrading.infra.universe.membership.top_n_by_weight`) returns. It is economic
    (it decides which names the book trades and so which constituent chains are captured), so it
    travels in ``config_hashes["universe"]`` rather than living as a ``.py`` literal.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    # NOTE: there is no `underlyings` list — the index registry below is the single source
    # of which instruments the platform tracks (T-index-only-refactor, owner coherence
    # principle: one universe source, no stale parallel list). Single-name tickers are index
    # constituents (data/reference/index_constituents), never standalone option underlyings.
    exchange: str = Field(min_length=1)
    # The default is for in-memory/test construction only: the YAML loader requires the
    # field present in universe.yaml, so the grid is never silently defaulted on the load
    # path (the same discipline ScenarioConfig uses).
    tenor_grid: _StrTuple = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")
    # The dispersion-basket selection size (S1): how many of an index's heaviest constituents by
    # index weight the top-N resolver returns (`top_n_by_weight`). Economic — it decides which
    # names the S1 dispersion book trades, hence which constituent chains get captured, so it
    # changes which records exist and folds into config_hashes["universe"] (ADR 0028 / C7), never
    # a `.py` literal. Default 10 = the course's top-10; the theory's top-50 is set in YAML.
    dispersion_top_n: int = Field(default=10, ge=1)
    # The index registry block (ADR 0035) — which indices the platform tracks, keyed by
    # symbol. Held here as the raw nested mapping (canonicalized to a stable, JSON-ready
    # form), NOT as the validated typed `IndexRegistry`: the typed parse + calendar-code
    # validation lives in the infra layer (it needs the `exchange_calendars` library, which
    # core must stay blind to). Keeping the block on `UniverseConfig` is what folds it into
    # `config_hashes["universe"]` (ADR 0035 §4) with no separate hash. The default is empty
    # — an absent `indices:` block is a valid empty registry. The `model_validator` below
    # deep-freezes it to the same stable JSON-ready form the hash depends on.
    indices: Mapping[str, Any] = Field(default_factory=dict)
    # The delta-band strike-selection policy (WS 1B, ADR 0028). A nested model: its delta
    # bound is economic and folds into the universe hash like any other field. The default
    # is for in-memory/test construction; the YAML loader requires the block present so the
    # economic bound is never silently defaulted on the load path.
    strike_selection: StrikeSelectionConfig = Field(
        default_factory=lambda: StrikeSelectionConfig(version="strike-selection-default")
    )
    # How many of an index's constituents the capture widens its option-chain scope to —
    # the point-in-time top-N *by index weight* (T-constituent-option-capture, TARGET §0/§7.4).
    # Economic: it decides which constituent names land option chains/surfaces each close, so it
    # changes which records exist and folds into config_hashes["universe"]. The course value is
    # top-10, the theory value top-50 (the dispersion-book sizing). The default is for in-memory/
    # test construction; the YAML carries the operative value. Must be >= 1 — a zero would mean
    # "capture no constituents", which is the index-only lane, expressed by not running the
    # constituent capture at all, never by a 0 here.
    constituent_top_n: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_tenor_grid_and_freeze_indices(self) -> UniverseConfig:
        if not self.tenor_grid:
            raise ValueError("tenor_grid must be non-empty")
        if len(set(self.tenor_grid)) != len(self.tenor_grid):
            raise ValueError("tenor_grid tenors must be unique")
        # Deep-freeze the indices block to the stable, JSON-ready form the universe hash
        # depends on (a read-only nested proxy), mirroring the loader's canonicalization so
        # an in-memory construction and a loaded one hash identically.
        object.__setattr__(self, "indices", _canonical_indices(self.indices))
        return self


class GridQcConfig(_ConfigModel):
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
      rather than silently defining its own band. Require
      ``-1 <= band_low_delta < band_high_delta <= 1``.
    - ``band_step`` — the projection's delta-band *emission* spacing: the WS-1F grid is built
      at ``[band_low_delta … −band_step, ATM, +band_step … band_high_delta]`` (e.g. the prof's
      ±30Δ *pas-2* grid). It lives here so the grid the projection **emits** and the grid this
      QC **validates** read one band definition and cannot drift — the projection axis is built
      via :meth:`ProjectionConfig.from_band` from these same three numbers (ADR 0028).
    - ``max_delta_step`` — the largest acceptable gap between consecutive selected deltas
      inside the band; a hole wider than this is a completeness breach. Set equal to
      ``band_step`` so the QC actually *forces* the emission step (a dropped point opens a
      ``2·band_step`` hole and fails) rather than tolerating a coarser grid.

    Held as a nested model on :class:`QcThresholdConfig` so it folds into
    ``config_hashes["qc"]`` with no separate hash. The default is for in-memory/test
    construction only.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    tenor_floors: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=lambda: {
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
    band_low_delta: float = Field(default=-0.30, ge=-1.0)
    band_high_delta: float = Field(default=0.30, le=1.0)
    band_step: float = Field(default=0.02, gt=0.0)
    max_delta_step: float = Field(default=0.25, gt=0.0)

    @model_validator(mode="after")
    def _check_band(self) -> GridQcConfig:
        if not self.band_low_delta < self.band_high_delta:
            raise ValueError("require band_low_delta < band_high_delta")
        if self.band_step > self.band_high_delta - self.band_low_delta:
            raise ValueError("band_step must be no wider than the band it samples")
        return self

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


class ContinuityQcConfig(_ConfigModel):
    """Collector-continuity cut-offs: gap counts and the coverage floor (ADR 0028).

    These decide whether a capture session is continuous enough to trust — so they are
    economic (they gate which sessions survive QC) and hashed config, never ``.py``
    literals:

    - ``max_gap_count`` — at most this many gap events in a session before it fails.
    - ``warn_gap_count`` — a gap count above this (but at or below ``max_gap_count``) warns.
      Must be ``<= max_gap_count`` so the warn band sits below the fail band.
    - ``min_coverage_ratio`` — the fraction of subscribed instruments that must actually be
      covered; below it the feed is too thin to trust. A ratio in ``[0, 1]``.

    Held as a nested model on :class:`QcThresholdConfig` so it folds into
    ``config_hashes["qc"]`` with no separate hash. The default is for in-memory/test
    construction only; the loader requires the block present in ``qc.yaml``.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_gap_count: int = Field(default=5, ge=0)
    warn_gap_count: int = Field(default=1, ge=0)
    min_coverage_ratio: float = Field(default=0.95, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_gap_bands(self) -> ContinuityQcConfig:
        if self.warn_gap_count > self.max_gap_count:
            raise ValueError("require warn_gap_count <= max_gap_count")
        return self


class ForwardEngineQcConfig(_ConfigModel):
    """Forward-stability and parity-residual cut-offs (ADR 0028).

    These decide whether a put-call-parity forward and its parity line are stable enough
    to trust — economic, so hashed config, never ``.py`` literals. Both residual cut-offs are
    **relative to the forward** (a fraction of ``F``), not absolute price points, so the same
    economic tolerance holds across a 200-pt single-name and a 7400-pt index — an absolute-$
    cut-off was an always-FAIL false positive on index options (T-qc-residual-units):

    - ``max_rel_residual_mad`` — the largest acceptable parity-line residual MAD as a fraction
      of the forward; above it the forward is unstable and the curve point is untrustworthy.
      Aligned to the forward engine's ``fair_rel_residual`` so the diagnostic self-label and this
      QC gate share one residual basis (poor label ⇔ QC fail).
    - ``min_forward_confidence`` — the lowest acceptable estimate confidence, in ``[0, 1]``.
    - ``max_rel_parity_residual`` — the largest acceptable single put-call-parity residual as a
      fraction of the forward.

    Held as a nested model on :class:`QcThresholdConfig` so it folds into
    ``config_hashes["qc"]`` with no separate hash. The default is for in-memory/test
    construction only; the loader requires the block present in ``qc.yaml``.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_rel_residual_mad: float = Field(default=0.01, gt=0.0)
    min_forward_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    max_rel_parity_residual: float = Field(default=0.02, gt=0.0)


class FitToleranceQcConfig(_ConfigModel):
    """IV-convergence and surface-fit cut-offs (ADR 0028).

    These decide whether a smile is fittable and a slice fit is tight enough to trust —
    economic, so hashed config, never ``.py`` literals:

    - ``max_non_convergence_ratio`` — the largest acceptable fraction of solver requests
      that did not converge; above it the smile is too holey to fit. A ratio in ``[0, 1]``.
    - ``max_surface_rmse`` — the largest acceptable per-slice RMSE (in total-variance units).

    Held as a nested model on :class:`QcThresholdConfig` so it folds into
    ``config_hashes["qc"]`` with no separate hash. The default is for in-memory/test
    construction only; the loader requires the block present in ``qc.yaml``.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_non_convergence_ratio: float = Field(default=0.10, ge=0.0, le=1.0)
    max_surface_rmse: float = Field(default=0.02, gt=0.0)


class AnomalyQcConfig(_ConfigModel):
    """Robust-z anomaly bands and the static-check MAD multiplier (ADR 0028).

    The rolling-baseline anomaly plane and the static anomaly check both decide whether a
    metric has shifted abnormally — economic, so hashed config, never ``.py`` literals:

    - ``mad_multiplier`` — the static-check cut-off: how many baseline MADs from the median
      a value may sit before it is a spike (the QC plane's ``detect_anomaly``).
    - ``warn_z`` — the rolling-baseline plane's WARN band: ``|robust z|`` at or above this
      many MADs warns.
    - ``fail_z`` — the FAIL band; must be ``>= warn_z`` so warn sits below fail.
    - ``min_baseline`` — fewer baseline points than this and the run cannot be judged
      (NO_BASELINE, never assumed normal). Must be ``>= 1``.

    Held as a nested model on :class:`QcThresholdConfig` so it folds into
    ``config_hashes["qc"]`` with no separate hash. The default is for in-memory/test
    construction only; the loader requires the block present in ``qc.yaml``.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    mad_multiplier: float = Field(default=5.0, gt=0.0)
    warn_z: float = Field(default=3.5, gt=0.0)
    fail_z: float = Field(default=5.0, gt=0.0)
    min_baseline: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_bands(self) -> AnomalyQcConfig:
        if self.fail_z < self.warn_z:
            raise ValueError("require fail_z >= warn_z")
        return self


class QcThresholdConfig(_ConfigModel):
    """Cut-offs that decide whether a quote or chain is usable.

    The flat scalar cut-offs (``max_spread_pct``…``min_chain_count``) gate the
    instrument-agnostic quote/chain checks. The nested blocks carry the rest, each folding
    into the same ``qc`` config hash with no separate hash:

    - ``grid`` (WS 1H) — the grid-aware cut-offs (per-tenor coverage floors + Δ-band window).
    - ``continuity`` — collector-continuity gap counts and the coverage floor.
    - ``forward_engine`` — forward-stability and parity-residual cut-offs.
    - ``fit_tolerance`` — IV-convergence and surface-fit cut-offs.
    - ``anomaly`` — robust-z anomaly bands and the static-check MAD multiplier.

    Every nested default is for in-memory/test construction; the loader requires each block
    present in ``qc.yaml`` so the economic cut-offs are never silently defaulted on the load
    path (ADR 0028).
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    max_spread_pct: float = Field(gt=0.0)
    max_quote_age_seconds: float = Field(gt=0.0)
    min_chain_count: int = Field(ge=1)
    grid: GridQcConfig = Field(default_factory=lambda: GridQcConfig(version="grid-qc-default"))
    continuity: ContinuityQcConfig = Field(
        default_factory=lambda: ContinuityQcConfig(version="continuity-qc-default")
    )
    forward_engine: ForwardEngineQcConfig = Field(
        default_factory=lambda: ForwardEngineQcConfig(version="forward-engine-qc-default")
    )
    fit_tolerance: FitToleranceQcConfig = Field(
        default_factory=lambda: FitToleranceQcConfig(version="fit-tolerance-qc-default")
    )
    anomaly: AnomalyQcConfig = Field(
        default_factory=lambda: AnomalyQcConfig(version="anomaly-qc-default")
    )


class SolverConfig(_ConfigModel):
    """How the implied-volatility inversion is run.

    ``vol_min``/``vol_max`` are the search bracket the bracketed solve runs on: a
    near-zero floor standing in for "zero vol", and a ceiling above any real vol beyond
    which a target is treated as unresolvable. They carry defaults for in-memory/test
    construction, but the YAML loader requires them present, so the economic bracket is
    never silently defaulted on the load path. Require ``0 < vol_min < vol_max``.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    iv_tolerance: float = Field(gt=0.0)
    max_iterations: int = Field(ge=1)
    vol_min: float = Field(default=1e-9, gt=0.0)
    vol_max: float = Field(default=5.0, gt=0.0)

    @model_validator(mode="after")
    def _check_bracket(self) -> SolverConfig:
        if not self.vol_min < self.vol_max:
            raise ValueError("need vol_min < vol_max")
        return self


class SurfaceConfig(_ConfigModel):
    """Bounds and tolerances for the SVI surface fit, plus the projection grid.

    The five parameter feasible ranges constrain the calibration search and back the
    bound-hit diagnostic; ``svi_bound_hit_tol`` is how close (relative to a range) a
    fitted parameter must sit to count as "at the bound"; ``svi_max_iterations`` caps the
    least-squares budget. Each ``*_bounds`` is a finite, strictly-increasing
    ``(low, high)`` pair. ``moneyness_buckets`` is the log-moneyness grid the regularized
    surface is projected and persisted onto — surface output, so it lives here. Authored
    in ``pricing.yaml`` under ``surface:``. (The minimum-points floor for SVI is a
    mathematical invariant — five parameters need five points — and stays a code
    constant, not a tunable.)
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    svi_a_bounds: _FloatPair
    svi_b_bounds: _FloatPair
    svi_rho_bounds: _FloatPair
    svi_m_bounds: _FloatPair
    svi_sigma_bounds: _FloatPair
    svi_bound_hit_tol: float = Field(gt=0.0)
    svi_max_iterations: int = Field(ge=1)
    # Min distinct strikes a slice needs before SVI is trusted; below it, the fit routes to
    # the labeled nonparametric fallback (blueprint 07 `min_points_per_slice`, ADR 0028 — the
    # routing threshold gets a typed home instead of the `MIN_POINTS_FOR_SVI` .py literal).
    # Floored at 5 = SVI's five parameters (the identifiability minimum, a math invariant that
    # stays the hard floor in `surfaces.svi`); set higher to demand more points before SVI.
    min_points_per_slice: int = Field(default=5, ge=5)
    # The log-moneyness grid the regularized surface is projected and persisted onto — one
    # SurfaceGrid cell per bucket. An economic policy, not a technical constant: it sets which
    # strike points every persisted surface is sampled at, kept strictly increasing and symmetric
    # about the ATM forward (0.0) so the grid is comparable across underlyings (ADR 0028 — the
    # projection grid gets a typed home instead of the `DEFAULT_MONEYNESS_BUCKETS` .py literal;
    # folds into config_hashes["pricing"], so a change to it now moves a hash that flags drift).
    moneyness_buckets: _FloatTuple = (-0.2, -0.1, 0.0, 0.1, 0.2)

    @model_validator(mode="after")
    def _check_bound_pairs(self) -> SurfaceConfig:
        for name in (
            "svi_a_bounds",
            "svi_b_bounds",
            "svi_rho_bounds",
            "svi_m_bounds",
            "svi_sigma_bounds",
        ):
            pair = getattr(self, name)
            if len(pair) != 2:
                raise ValueError(f"{name} must be a (low, high) pair")
            low, high = pair
            if not low < high:
                raise ValueError(f"{name} need low < high")
        return self

    @model_validator(mode="after")
    def _check_moneyness_buckets(self) -> SurfaceConfig:
        buckets = self.moneyness_buckets
        if not buckets:
            raise ValueError("moneyness_buckets must be non-empty")
        if list(buckets) != sorted(buckets) or len(set(buckets)) != len(buckets):
            raise ValueError("moneyness_buckets must be strictly increasing")
        if 0.0 not in buckets:
            raise ValueError("moneyness_buckets must include 0.0 (the ATM/forward point)")
        # Negation is exact for IEEE floats, so a symmetric grid maps onto itself exactly.
        if tuple(sorted(-k for k in buckets)) != tuple(buckets):
            raise ValueError("moneyness_buckets must be symmetric about 0.0")
        return self


class ForwardConfig(_ConfigModel):
    """Confidence/quality heuristics for the put-call-parity forward estimate.

    These map a maturity's used-pair count and relative fit residual to a quality label
    and a 0..1 confidence every downstream consumer trusts. Authored in ``pricing.yaml``
    under ``forward:``. (The minimum-pairs floor for the regression — two unknowns need
    two equations — and the residual float-noise floor are mathematical/precision
    invariants and stay code constants.)
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    good_rel_residual: float = Field(gt=0.0)
    fair_rel_residual: float = Field(gt=0.0)
    full_credit_pairs: float = Field(gt=0.0)
    rel_residual_halflife: float = Field(gt=0.0)
    single_pair_confidence: float = Field(ge=0.0, le=1.0)
    # Explicit interest rate r (flat, MVP), the blueprint's *input* to the carry/dividend
    # split q(T) = r − ln(F/S)/T (Eq 5) and the rho basis — owner-asked 2026-06-13: the rate
    # must be a modifiable parameter, not an implicit back-derived constant (T-explicit-rate-
    # parameter). ``None`` = fall back to the parity-DF-implied rate r = −ln(DF)/T (the prior
    # behaviour, byte-identical), so a config that does not set it is unchanged; a value
    # overrides the split's rate (a curve r(T) is the later form). Negative rates are valid.
    rate: float | None = None


class StressSurfaceConfig(_ConfigModel):
    """The ±range stress *surface* grid — the 2B (spot × vol) PnL surface (ADR 0006/0028).

    Distinct from the ``scenario`` families (a spot family, a vol family, one crash, a
    time roll): this is the **full cartesian** grid the stress page reprices. Each axis is
    a *symmetric* shock range, sampled on an *odd* number of points so the centre (0 shock)
    — the cell the page pins to ≈0 PnL — is always present:

    * ``spot_shock_abs`` is the symmetric magnitude of the **relative** spot axis
      (``spot_shock ∈ [-abs, +abs]``, the engine's ``new_spot = spot*(1+s)`` convention).
    * ``vol_shock_abs`` is the symmetric magnitude of the **additive** vol axis
      (``new_vol = vol + v``).
    * ``spot_steps`` / ``vol_steps`` are the number of grid points per axis — odd, so 0 is
      sampled; ``1`` is the degenerate centre-only column.

    Every value is config (ADR 0028): it folds into ``config_hashes["scenarios"]`` and into
    :func:`effective_scenario_version`, so the production ±50%/±50% grid is a YAML edit, never
    a ``.py`` literal. The defaults are non-production placeholders for in-memory / test
    construction only — the load path requires the block (see ``loader._build_scenario``).
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    # Magnitude must be non-negative (the axis is symmetric ±abs).
    spot_shock_abs: float = Field(default=0.10, ge=0.0)
    vol_shock_abs: float = Field(default=0.10, ge=0.0)
    spot_steps: int = Field(default=3, ge=1)
    vol_steps: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _check_steps_odd(self) -> StressSurfaceConfig:
        for name in ("spot_steps", "vol_steps"):
            steps = getattr(self, name)
            if steps % 2 == 0:
                raise ValueError(f"{name} must be odd so the centre (0 shock) cell is sampled")
        return self


class ScenarioConfig(_ConfigModel):
    """The stress grid applied by the risk engine.

    ``roll_down_days`` carries the (default-bearing) construction parameter for the
    time-roll family of the grid (each a positive day count). The default is for
    in-memory/test construction only: the YAML loader still requires the field present in
    ``scenarios.yaml``, so an economic field is never silently defaulted on the load path.

    Empty shock tuples are valid — a grid with no spot/vol shocks is just the time-roll
    scenario; only the shock *values* are constrained (they must be finite, enforced by
    :func:`canonical_json`'s ``allow_nan=False`` at hash time).

    ``rate_shocks`` carries the **rate-shock family** (the course's third stress axis —
    ``AlgoTradingCourse2-Consignes`` l.117-120). Each value is an **additive** absolute
    shift in the continuously-compounded rate (e.g. ``0.0025`` = +25 bp), the same additive
    convention as ``vol_shocks`` and the natural unit for the forward-fixed rho (per 1.00 of
    rate). Its default is the empty tuple — **no rate family**, so every grid built without
    a configured rate axis is byte-identical to before this field existed (backward-compatible
    construction). When non-empty it folds into the construction hash, so a rate axis cannot
    be added without moving the persisted ``effective_scenario_version``.

    ``stress_surface`` is the 2B cartesian (spot × vol) surface grid (see
    :class:`StressSurfaceConfig`). Like ``roll_down_days`` its default is a placeholder for
    in-memory construction; the load path (``loader._build_scenario``) requires the
    ``stress_surface:`` block, so the production ±50% grid is never silently defaulted. It
    canonicalizes into ``config_hashes["scenarios"]`` like every other field.
    """

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    spot_shocks: _FloatTuple
    vol_shocks: _FloatTuple
    rate_shocks: _FloatTuple = ()
    roll_down_days: _IntTuple = (1,)
    stress_surface: StressSurfaceConfig = Field(
        default_factory=lambda: StressSurfaceConfig(version="stress-surface-default")
    )

    @model_validator(mode="after")
    def _check_roll_down_days(self) -> ScenarioConfig:
        for days in self.roll_down_days:
            if days <= 0:
                raise ValueError("roll_down_days must be a positive day count")
        return self


GAMMA_NORMALISATIONS = ("one_pct", "one_dollar")
THETA_DAY_COUNTS = (365, 252)


class MonetizationConfig(_ConfigModel):
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

    model_config = _SECTION_CONFIG

    version: str = Field(min_length=1)
    gamma_normalisation: Literal["one_pct", "one_dollar"] = "one_pct"
    theta_day_count: Literal[365, 252] = 365


class PlatformConfig(_ConfigModel):
    """The whole economic configuration: the versioned typed sections."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    universe: UniverseConfig
    qc_threshold: QcThresholdConfig
    solver: SolverConfig
    surface: SurfaceConfig
    forward: ForwardConfig
    scenario: ScenarioConfig
    # Default for in-memory/test construction; the YAML loader requires the
    # `monetization:` block present in scenarios.yaml (it is an economic input).
    monetization: MonetizationConfig = Field(
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


def _canonical_indices(value: Any) -> Any:
    """Return a deeply-immutable, JSON-ready copy of the indices block for stable hashing."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _canonical_indices(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_indices(v) for v in value)
    return value


def _canonical(value: Any) -> Any:
    """Turn a config value into something with one, stable JSON form.

    Tuples and lists become lists; pydantic models and frozen dataclasses and mappings
    become dicts (their values canonicalized too); floats are left to JSON (with ``-0.0``
    collapsed onto ``0.0``). The point is that the same logical config always produces
    byte-identical JSON, whatever container the value happens to live in. Dataclasses are
    handled alongside pydantic models because reusable consumers (infra's
    ``ProjectionConfig``) still hash a frozen dataclass through this same canonical form.
    """
    if isinstance(value, BaseModel):
        return {name: _canonical(getattr(value, name)) for name in type(value).model_fields}
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


def object_config_hash(value: Any) -> str:
    """SHA-256 over the canonical JSON of any config-shaped object.

    The one hash for a frozen config object outside :class:`PlatformConfig` — e.g.
    infra's ``ProjectionConfig`` or a ``MonetizationConfig`` hashed standalone (M14).
    Uses :func:`canonical_json` (the typed-config convention: ``-0.0`` collapsed,
    NaN/Inf rejected) and :func:`~algotrading.core.hashing.sha256_hex`, so it is
    byte-identical to the inlined ``sha256(canonical_json(...))`` copies it replaced.
    """
    return sha256_hex(canonical_json(value))


def config_hash(config: PlatformConfig) -> str:
    """Hash the whole config. Moves when any economic field in any section moves."""
    return object_config_hash(config)


def section_hash(config: PlatformConfig, section: str) -> str:
    """Hash one named section. Moves only when that section's fields move.

    Raises ``KeyError`` for an unknown section name rather than guessing, so a
    typo fails loudly instead of silently hashing nothing.
    """
    if section not in SECTION_NAMES:
        raise KeyError(section)
    return object_config_hash(getattr(config, section))


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
        bundle: object_config_hash({name: getattr(config, name) for name in names})
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
    return sha256_hex(canonical_dumps({str(k): str(v) for k, v in parts.items()}))
