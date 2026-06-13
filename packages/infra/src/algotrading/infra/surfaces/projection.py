"""Project a fitted vol surface onto the pinned tenor × delta-band analytics grid (WS 1F).

This is the cross-maturity regrid the rest of the surface engine did not do. ``fit.py``
fits one SVI smile per listed maturity and projects a single slice onto a log-moneyness
bucket grid; this module takes the *whole* set of per-maturity fits and produces, for one
underlying at one snapshot, a deterministic grid over the **pinned tenor set** crossed
with a **delta band** — the 30Δ-put → ATM → 30Δ-call window, with the ATM pillar emitted as
both a call (``atm``) and a put (``atmp``) at the one ATM-forward strike so an ATM straddle's
two legs are both in the grid. Every cell carries the
fitted IV, the model price, and the full Greeks in **both** representations side by side:
the raw decimal per-unit Greeks (the source of truth) and the derived dollar Greeks, each
dollar number tagged with an explicit unit string (OQ-1 / P0.2, ADR 0036).

The two regrids, both no-look-ahead (every cell uses only the snapshot's fits and market
state, never a future snapshot):

* **Tenor.** The pinned tenors rarely coincide with listed expiries, so the smile is
  regridded along maturity in **total-variance** space (calendar-no-arb-respecting, Eq 21
  / Eq 22) by reusing :func:`surfaces.fit.interpolate_total_variance` — never in raw vol,
  which can violate calendar no-arb. A pinned tenor outside the fitted maturity span is a
  **labeled gap** (:class:`ProjectionGap`), never a silent extrapolation or a bare NaN
  (1H consumes these gaps).

* **Delta.** For each (tenor, delta-band point) the option delta is inverted against the
  fitted IV to recover the strike/log-moneyness, in the **spot-delta convention** of
  :mod:`pricing.black76` (built at ``carry == 0`` so spot and forward delta coincide), so
  the band lands on the right strikes and the IV used to price a cell is the IV at the
  solved strike. A target delta that cannot be solved inside the fitted strike span is a
  labeled gap, not a guess.

The grid axes (tenor grid, delta-band axis) and the interpolation rule are validated
config (:class:`ProjectionConfig`); the gamma/theta $-convention forks come from
:class:`~algotrading.core.config.MonetizationConfig`. All of these enter the provenance
``config_hashes`` so the grid is reproducible byte-for-byte across processes.

Pure throughout: ``calc_ts`` and the snapshot timestamps are injected, no wall-clock read.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.config import (
    ConfigFieldError,
    MonetizationConfig,
    object_config_hash,
)
from algotrading.core.provenance import ProvenanceStamp, snapshot_stamp, source_ref
from algotrading.infra.contracts import ProjectedOptionAnalytics
from algotrading.infra.pricing import (
    PRICER_VERSION,
    UNIT_STRINGS,
    dollar_greeks,
    from_forward,
    price_european,
)

from .fit import METHOD_INSUFFICIENT, SliceFit, interpolate_total_variance
from .market_state import SnapshotMarketState
from .svi import SURFACE_VERSION

# Bump only on a real change to the projection logic, never on config.
# 1.1.0: discount factors interpolated from the listed-expiry curve at the pinned tenor
# (flat-forward in -ln DF) instead of the silent rate-free fallback on a float-key miss
# (F-SURF-01).
PROJECTION_VERSION = "projection-1.1.0"

# PRICER_VERSION is imported from the pricing engine (the single home, M14) so a cell's
# ``pricer_version`` can never silently fork from ``PricingResult.pricer_version``.

# The pinned tenor grid (P0.1 / OQ-4, blueprint Part IX data dictionary, ADR 0011), with
# each label's ACT/365 year fraction. This is the authoritative *order* and membership the
# projection enforces; a config drift to a different set fails loudly (ProjectionConfig).
PINNED_TENORS: tuple[str, ...] = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")
_TENOR_YEARS: dict[str, float] = {
    "10d": 10.0 / 365.0,
    "1m": 1.0 / 12.0,
    "3m": 3.0 / 12.0,
    "6m": 6.0 / 12.0,
    "12m": 1.0,
    "18m": 1.5,
    "2y": 2.0,
    "3y": 3.0,
}

# The delta-band axis is generated from a signed band (low_delta, high_delta, step), not
# hand-listed: :func:`delta_band_axis` expands it into parallel (labels, targets) tuples
# spanning the 30Δ put through ATM to the 30Δ call (blueprint OQ-4 window). A put target is
# negative, a call positive; ATM is the 0.50-delta pillar (signed 0.0 as the band centre).
# The labels are the band names the front renders; the magnitude is the |delta| solved for.
#
# ``atm`` and ``atmp`` are the call and the put at the SAME ATM-forward strike (both solve from
# target 0.0): the call (``atm``) and the put (``atmp``) that compose an ATM straddle. The option
# right is taken from the label suffix (``…p`` → put, ``…c`` → call), so the two ATM pillars are
# a call and a put at one strike — see :func:`_option_right_for_band`. A straddle is the two of
# them summed (delta-neutral, 2× gamma/vega); without ``atmp`` the grid could not express it.
#
# The economic band lives in typed config (``qc_threshold.grid`` band edges + step, ADR 0028),
# read by the driver and passed through :meth:`ProjectionConfig.from_band`. These module
# constants are the in-memory/test default (the prof's pinned ±30Δ pas-2 window), generated by
# the same expander so there is never a hand-written band literal to drift from the generator.
class ProjectionConfigError(ConfigFieldError):
    """A projection-config field was malformed."""


_DEFAULT_BAND_LOW_DELTA = -0.30
_DEFAULT_BAND_HIGH_DELTA = 0.30
_DEFAULT_BAND_STEP = 0.02


def delta_band_axis(
    *, band_low_delta: float, band_high_delta: float, band_step: float,
) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """Expand a signed delta band into the parallel ``(labels, targets)`` projection axis.

    Builds the contiguous block **30Δ put → ATM → 30Δ call** at a fixed step: put targets
    from ``band_low_delta`` (e.g. ``-0.30``) up to ``-band_step`` in ``band_step`` increments
    (deepest first: ``30dp, 28dp, … , 02dp``), then the two ATM pillars (``atm`` call and
    ``atmp`` put, both at target ``0.0`` / the one ATM-forward strike — the straddle legs),
    then call targets from ``+band_step`` up to ``band_high_delta`` (``02dc, … , 30dc``). For
    the prof's pinned ``(-0.30, +0.30, 0.02)`` that is 15 puts + atm + atmp + 15 calls = 32
    cells per spanned tenor (the ±30Δ *pas-2* grid the front renders).

    The band must be expressible in hundredths of a delta (0.01 granularity — deltas are
    quoted as integer Δ points, ``30Δ``/``2Δ``); a low/high/step that is not a whole number
    of hundredths, or a step that does not evenly divide each edge, raises
    :class:`ProjectionConfigError` rather than silently emitting an off-grid axis (ADR 0028).
    Labels are ``f"{|Δ|·100:02d}dp"`` / ``…dc`` so they are stable and unique.
    """
    low_c = _centi_delta(band_low_delta, "band_low_delta")
    high_c = _centi_delta(band_high_delta, "band_high_delta")
    step_c = _centi_delta(band_step, "band_step")
    if step_c <= 0:
        raise ProjectionConfigError("projection", "band_step", band_step, "must be > 0")
    if low_c >= 0 or high_c <= 0:
        raise ProjectionConfigError(
            "projection", "band_targets", (band_low_delta, band_high_delta),
            "need band_low_delta < 0 < band_high_delta (puts below ATM, calls above)",
        )
    if (-low_c) % step_c != 0 or high_c % step_c != 0:
        raise ProjectionConfigError(
            "projection", "band_step", band_step,
            f"must evenly divide both band edges ({band_low_delta}, {band_high_delta})",
        )
    labels: list[str] = []
    targets: list[float] = []
    for mag_c in range(-low_c, 0, -step_c):  # deepest put first: 30dp … 02dp
        labels.append(f"{mag_c:02d}dp")
        targets.append(-mag_c / 100.0)
    labels.extend(("atm", "atmp"))  # the ATM call and the ATM put at the one ATM-forward strike
    targets.extend((0.0, 0.0))
    for mag_c in range(step_c, high_c + 1, step_c):  # 02dc … 30dc
        labels.append(f"{mag_c:02d}dc")
        targets.append(mag_c / 100.0)
    return tuple(labels), tuple(targets)


def _centi_delta(value: float, field: str) -> int:
    """A delta expressed in whole hundredths (``0.30 → 30``), or raise if off the 0.01 grid."""
    scaled = value * 100.0
    nearest = round(scaled)
    if not math.isclose(scaled, nearest, abs_tol=1e-9):
        raise ProjectionConfigError(
            "projection", field, value,
            "delta band must be a whole number of hundredths (0.01 step)",
        )
    return int(nearest)


_DEFAULT_BAND_LABELS, _DEFAULT_BAND_TARGETS = delta_band_axis(
    band_low_delta=_DEFAULT_BAND_LOW_DELTA,
    band_high_delta=_DEFAULT_BAND_HIGH_DELTA,
    band_step=_DEFAULT_BAND_STEP,
)

# Newton/bisection budget and tolerance for the delta -> strike inversion. Numerical
# invariants (how hard we try, how close is "solved"), not economic tunables, so they stay
# code constants per the config standard's invariant carve-out.
_DELTA_SOLVE_MAX_ITER = 100
_DELTA_SOLVE_TOL = 1e-10


@dataclass(frozen=True, slots=True)
class ProjectionConfig:
    """The validated projection axes and interpolation rule (DI'd into the projection).

    ``tenor_grid`` is the ordered pinned tenor set; it must equal :data:`PINNED_TENORS`
    exactly (order and membership) so a drift to a different grid fails loudly rather than
    silently quoting a wrong axis. ``band_labels``/``band_targets`` are the delta-band
    axis: parallel tuples of label and signed target delta, defaulting to the 30Δ-put →
    ATM → 30Δ-call window. ``interpolation`` names the cross-maturity regrid rule
    (``total_variance_linear`` — linear in total variance between bracketing maturities,
    the only calendar-no-arb rule we ship). ``clamp_to_span`` is *off* by default: a tenor
    outside the fitted span is a labeled gap, never a silent clamp/extrapolation.

    Hashed via :meth:`config_hash` and folded into the provenance ``config_hashes`` under
    the ``projection`` key, so the tenor grid, delta-band axis, and interpolation rule are
    all reproducible. The gamma/theta $-flags live in the separate ``scenarios`` bundle
    (:class:`MonetizationConfig`) and enter ``config_hashes["scenarios"]``.
    """

    version: str
    tenor_grid: tuple[str, ...] = PINNED_TENORS
    band_labels: tuple[str, ...] = _DEFAULT_BAND_LABELS
    band_targets: tuple[float, ...] = _DEFAULT_BAND_TARGETS
    interpolation: str = "total_variance_linear"
    clamp_to_span: bool = False

    def __post_init__(self) -> None:
        if not self.version:
            raise ProjectionConfigError("projection", "version", self.version, "must be non-empty")
        if self.tenor_grid != PINNED_TENORS:
            raise ProjectionConfigError(
                "projection",
                "tenor_grid",
                self.tenor_grid,
                f"must be exactly the pinned tenor set {PINNED_TENORS} in that order",
            )
        if len(self.band_labels) != len(self.band_targets):
            raise ProjectionConfigError(
                "projection",
                "band_labels",
                self.band_labels,
                "band_labels and band_targets must have the same length",
            )
        if not self.band_labels:
            raise ProjectionConfigError(
                "projection", "band_labels", self.band_labels, "must be non-empty"
            )
        if len(set(self.band_labels)) != len(self.band_labels):
            raise ProjectionConfigError(
                "projection", "band_labels", self.band_labels, "band labels must be unique"
            )
        for target in self.band_targets:
            if not math.isfinite(target) or not -1.0 < target < 1.0:
                raise ProjectionConfigError(
                    "projection", "band_targets", target, "each target delta must lie in (-1, 1)"
                )
        if self.interpolation != "total_variance_linear":
            raise ProjectionConfigError(
                "projection",
                "interpolation",
                self.interpolation,
                "only 'total_variance_linear' is supported",
            )

    def config_hash(self) -> str:
        """A deterministic SHA-256 over the projection axes (the ``projection`` bundle).

        Delegates to :func:`~algotrading.core.config.object_config_hash` (the typed-config
        canonical-JSON convention), so ``-0.0`` collapses onto ``0.0`` and a NaN/Inf is
        rejected — the hash is byte-identical across processes without ``PYTHONHASHSEED``
        (the C7 hardening the golden test pins).
        """
        return object_config_hash(self)

    @classmethod
    def from_band(
        cls,
        *,
        version: str,
        band_low_delta: float,
        band_high_delta: float,
        band_step: float,
        tenor_grid: tuple[str, ...] = PINNED_TENORS,
        interpolation: str = "total_variance_linear",
        clamp_to_span: bool = False,
    ) -> ProjectionConfig:
        """Build a config whose delta-band axis is expanded from a signed band + step.

        The economic axis home (ADR 0028): the driver reads ``band_low_delta`` /
        ``band_high_delta`` / ``band_step`` from typed config (``qc_threshold.grid``) and hands
        them here, so the production grid is a YAML edit, never a ``.py`` literal at the call
        site. :func:`delta_band_axis` does the expansion (validated, hundredths-grid), and the
        resulting ``band_labels``/``band_targets`` fold into ``config_hash`` exactly as a
        hand-passed axis would — the band is reproducible byte-for-byte.
        """
        band_labels, band_targets = delta_band_axis(
            band_low_delta=band_low_delta, band_high_delta=band_high_delta, band_step=band_step,
        )
        return cls(
            version=version, tenor_grid=tenor_grid, band_labels=band_labels,
            band_targets=band_targets, interpolation=interpolation, clamp_to_span=clamp_to_span,
        )


def tenor_years(label: str) -> float:
    """Return the ACT/365 year fraction for a pinned tenor label, or raise.

    The single home of the tenor-label → year-fraction map (blueprint data dictionary).
    Raises :class:`ProjectionConfigError` for a label outside the pinned eight rather than
    guessing a fraction.
    """
    try:
        return _TENOR_YEARS[label]
    except KeyError:
        raise ProjectionConfigError(
            "projection", "tenor_label", label, f"unknown tenor; must be one of {PINNED_TENORS}"
        ) from None


@dataclass(frozen=True, slots=True)
class ProjectionGap:
    """A labeled gap in the grid: a cell that could not be produced, and why.

    Emitted instead of a bare NaN so 1H's QC (coverage floor per tenor, Δ-band
    completeness) acts on a structured fact, never a silent hole. ``reason_code`` is the
    machine-readable why (``tenor_beyond_span``, ``delta_out_of_band``, ``no_curve``);
    ``detail`` is the one-line headline naming the offending axis point.
    """

    underlying: str
    tenor_label: str
    delta_band: str
    target_delta: float
    reason_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """What projecting one underlying's snapshot produces: the grid cells and the gaps.

    ``cells`` are the produced :class:`ProjectedOptionAnalytics` rows (one per solvable
    (tenor, delta-band) point); ``gaps`` are the labeled holes. The pairing exists so a
    caller (the 1G actor) persists ``cells`` and routes ``gaps`` to triage without having
    to re-derive which points were producible.
    """

    cells: tuple[ProjectedOptionAnalytics, ...]
    gaps: tuple[ProjectionGap, ...]


def _usable_span(slices: Sequence[SliceFit]) -> tuple[float, float] | None:
    """The (min, max) fitted maturity over slices that carry a curve, or None if none do."""
    usable = [s.maturity_years for s in slices if s.method != METHOD_INSUFFICIENT]
    if not usable:
        return None
    return min(usable), max(usable)


def _strike_span(slices: Sequence[SliceFit]) -> tuple[float, float] | None:
    """The (low, high) log-moneyness bracket the fitted slices cover, or None.

    Used to refuse a delta target that would land outside the fitted strike data — a
    labeled gap, never a silent extrapolation past the observed strikes. Spans the union of
    the curve-bearing slices' observed log-moneyness. Every curve-bearing slice (SVI *or*
    nonparametric) carries its sorted observed ``nonparametric_ks``, so this returns ``None``
    exactly when :func:`_usable_span` does — there is no fitted maturity without strikes.
    """
    lows: list[float] = []
    highs: list[float] = []
    for s in slices:
        if s.method == METHOD_INSUFFICIENT or not s.nonparametric_ks:
            continue
        lows.append(s.nonparametric_ks[0])
        highs.append(s.nonparametric_ks[-1])
    if not lows:
        return None
    return min(lows), max(highs)


def _iv_at(slices: Sequence[SliceFit], k: float, maturity_years: float) -> float:
    """The fitted implied vol at (log-moneyness k, maturity) from the regridded surface.

    Reads total variance off the calendar-no-arb regrid (linear in total variance between
    bracketing maturities, Eq 22) and converts to vol ``sqrt(w / T)`` — never interpolating
    raw vol directly. Clamps the (interpolated) total variance at zero so a tiny negative
    float never produces a NaN vol.
    """
    w = max(interpolate_total_variance(slices, k, maturity_years), 0.0)
    return math.sqrt(w / maturity_years) if maturity_years > 0.0 else 0.0


def _call_nd1(*, forward: float, strike: float, maturity_years: float,
              volatility: float, discount_factor: float) -> float:
    """Undiscounted forward call delta ``N(d1)`` via the pricing engine, carry == 0.

    The engine is the single source of the delta (spot-delta convention); dividing its
    discounted spot delta by the discount factor recovers the undiscounted ``N(d1)``. This
    is exactly the 1B convention so the band lands consistently. ``N(d1)`` is monotone
    decreasing in strike, which is what makes the delta -> strike solve well-posed.
    """
    state = from_forward(
        forward=forward, strike=strike, maturity_years=maturity_years,
        volatility=volatility, discount_factor=discount_factor, option_right="C", spot=None,
    )
    return price_european(state).delta / discount_factor


def _solve_strike_for_delta(
    slices: Sequence[SliceFit],
    *,
    target_delta: float,
    forward: float,
    maturity_years: float,
    discount_factor: float,
    span: tuple[float, float],
) -> float | None:
    """Invert the signed target delta to a strike against the fitted IV, or None if out of band.

    The signed target delta maps to a target undiscounted call ``N(d1)``: a call target
    ``+d`` is ``N(d1) = d`` (a high strike); a put target ``-d`` is ``N(d1) = 1 - d`` (a low
    strike, where the put-delta magnitude ``1 - N(d1)`` equals ``d``); ATM (``target == 0``)
    is ``N(d1) = 0.5``. Because the IV varies with strike (the smile), this is a fixed point:
    at each candidate log-moneyness ``k`` the IV is read off the regridded surface and the
    call ``N(d1)`` recomputed. ``N(d1)`` is monotone in ``k``, so a bisection over the fitted
    strike span converges; a target whose ``N(d1)`` is not bracketed by the span's endpoints
    is **out of band** and returns None (a labeled gap), never an extrapolated strike.
    """
    target_nd1 = 0.5 if target_delta == 0.0 else (
        target_delta if target_delta > 0.0 else 1.0 + target_delta
    )
    low_k, high_k = span

    def nd1_at(k: float) -> float:
        strike = forward * math.exp(k)
        vol = _iv_at(slices, k, maturity_years)
        if vol <= 0.0:
            # No usable vol at this point — treat as unbounded so the bracket check fails
            # cleanly rather than dividing by zero inside the engine.
            return math.nan
        return _call_nd1(
            forward=forward, strike=strike, maturity_years=maturity_years,
            volatility=vol, discount_factor=discount_factor,
        )

    # N(d1) decreases as k (and the strike) rises: it is ~1 at the low-strike end and ~0 at
    # the high-strike end. The target must sit between the two endpoints to be in band.
    nd1_low, nd1_high = nd1_at(low_k), nd1_at(high_k)
    if math.isnan(nd1_low) or math.isnan(nd1_high):
        return None
    hi_val, lo_val = nd1_low, nd1_high  # hi_val at low_k (larger N(d1)), lo_val at high_k
    if not (lo_val - _DELTA_SOLVE_TOL <= target_nd1 <= hi_val + _DELTA_SOLVE_TOL):
        return None

    a, b = low_k, high_k
    fa = nd1_at(a) - target_nd1
    for _ in range(_DELTA_SOLVE_MAX_ITER):
        mid = 0.5 * (a + b)
        fmid = nd1_at(mid) - target_nd1
        if math.isnan(fmid):
            return None
        if abs(fmid) <= _DELTA_SOLVE_TOL or 0.5 * (b - a) <= _DELTA_SOLVE_TOL:
            return mid
        if (fa > 0.0) == (fmid > 0.0):
            a, fa = mid, fmid
        else:
            b = mid
    return 0.5 * (a + b)


def _option_right_for_band(delta_band: str, target_delta: float) -> str:
    """The option right for a band cell: the label **suffix** governs, the sign is the fallback.

    A ``…p`` label is a put, a ``…c`` label a call; a label with no side suffix (``atm``) falls
    back to the signed target (negative → put, otherwise call). This is what lets the ATM put
    (``atmp``, target 0.0) be a **put** while the ATM call (``atm``, target 0.0) stays a **call**
    — they share the ATM-forward strike, so the two summed are a straddle. For every other band
    the suffix and the sign agree, so this is behaviour-preserving for the existing grid.
    """
    if delta_band.endswith("p"):
        return "P"
    if delta_band.endswith("c"):
        return "C"
    return "P" if target_delta < 0.0 else "C"


def _projection_stamp(
    slices: Sequence[SliceFit],
    *,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> ProvenanceStamp:
    """One snapshot-wide stamp shared by every cell, naming the fitted slices' raw sources.

    Every cell of a snapshot's grid shares one provenance stamp (the grid is one
    computation), naming the IvPoints that fed each fitted slice as sources — the same
    lineage the surface fit records, so a cell traces back to the raw observations.
    """
    refs = tuple(
        source_ref("iv_points", source_snapshot_ts, point.contract_key)
        for s in slices
        for point in s.raw_points
    )
    return snapshot_stamp(
        calc_ts=calc_ts,
        code_version=PROJECTION_VERSION,
        config_hashes=config_hashes,
        source_snapshot_ts=source_snapshot_ts,
        source_records=refs,
    )


def merged_config_hashes(
    base: Mapping[str, str],
    *,
    projection: ProjectionConfig,
    monetization: MonetizationConfig,
) -> dict[str, str]:
    """Fold the projection-axis and $-flag hashes into a base ``config_hashes`` mapping.

    The complete reproducibility key for a grid cell: the caller's existing per-bundle
    hashes (``universe`` — tenor grid + delta bound; ``pricing`` — surface fit), plus the
    ``projection`` hash (delta-band axis, interpolation rule) and the ``scenarios`` hash
    (gamma/theta $-flags). Built here so the projection has one home for "which config
    shaped this cell". A NaN/Inf or ``-0.0`` cannot poison it (canonical_json discipline).
    """
    merged = dict(base)
    merged["projection"] = projection.config_hash()
    merged.setdefault("scenarios", object_config_hash(monetization))
    return merged


def project_grid(
    slices: Sequence[SliceFit],
    market: SnapshotMarketState,
    *,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    projection: ProjectionConfig,
    monetization: MonetizationConfig,
    config_hashes: Mapping[str, str],
) -> ProjectionResult:
    """Project one underlying's fitted surface onto the pinned tenor × delta-band grid.

    The 1F entrypoint, a pure function of the snapshot's fits + market state + config. For
    each pinned tenor it regrids the smile in total-variance space (calendar-no-arb, Eq 22)
    and, for each delta-band point, inverts the option delta to a strike against the fitted
    IV (spot-delta convention), prices the cell with Black-76, takes the decimal Greeks as
    source of truth and derives the dollar layer (gamma/theta flags from ``monetization``,
    unit strings attached). A tenor beyond the fitted maturity span, or a delta target
    outside the fitted strike span, is a labeled :class:`ProjectionGap`, never a silent
    extrapolation or a bare NaN. The cell ordering is a pure function of the config axes
    (tenor order, then band order), independent of the order the input slices arrive in.

    No look-ahead: every cell uses only ``slices`` (this snapshot's fits) and ``market``
    (this snapshot's state); the function reads no wall clock and no later observation.
    """
    full_hashes = merged_config_hashes(
        config_hashes, projection=projection, monetization=monetization
    )
    provenance = _projection_stamp(
        slices, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts, config_hashes=full_hashes
    )
    span = _usable_span(slices)
    strike_span = _strike_span(slices)

    cells: list[ProjectedOptionAnalytics] = []
    gaps: list[ProjectionGap] = []
    for tenor_label in projection.tenor_grid:
        maturity = tenor_years(tenor_label)
        # span and strike_span are both None exactly when no slice carries a curve (every
        # curve-bearing slice has observed strikes), so this one guard covers both.
        if span is None or strike_span is None:
            for label, target in zip(projection.band_labels, projection.band_targets, strict=True):
                gaps.append(ProjectionGap(
                    underlying=market.underlying, tenor_label=tenor_label, delta_band=label,
                    target_delta=target, reason_code="no_curve",
                    detail=f"{market.underlying} {tenor_label}: no fitted slice carries a curve",
                ))
            continue
        low_m, high_m = span
        if not projection.clamp_to_span and not (low_m <= maturity <= high_m):
            for label, target in zip(projection.band_labels, projection.band_targets, strict=True):
                gaps.append(ProjectionGap(
                    underlying=market.underlying, tenor_label=tenor_label, delta_band=label,
                    target_delta=target, reason_code="tenor_beyond_span",
                    detail=(
                        f"{market.underlying} {tenor_label} (T={maturity:.4f}y) is outside the "
                        f"fitted span [{low_m:.4f}, {high_m:.4f}]y — no extrapolation"
                    ),
                ))
            continue

        discount_factor = market.discount_factor_for(tenor_label, maturity)
        forward = market.spot  # carry == 0: forward == spot (the convention pin)
        for label, target in zip(projection.band_labels, projection.band_targets, strict=True):
            k = _solve_strike_for_delta(
                slices, target_delta=target, forward=forward, maturity_years=maturity,
                discount_factor=discount_factor, span=strike_span,
            )
            if k is None:
                gaps.append(ProjectionGap(
                    underlying=market.underlying, tenor_label=tenor_label, delta_band=label,
                    target_delta=target, reason_code="delta_out_of_band",
                    detail=(
                        f"{market.underlying} {tenor_label} {label} (Δ={target:+.2f}) lands "
                        "outside the fitted strike span — labeled gap, no guess"
                    ),
                ))
                continue
            cells.append(_build_cell(
                slices, market=market, tenor_label=tenor_label, maturity=maturity,
                discount_factor=discount_factor, forward=forward, k=k,
                delta_band=label, target_delta=target, monetization=monetization,
                snapshot_ts=snapshot_ts, source_snapshot_ts=source_snapshot_ts,
                provenance=provenance,
            ))

    return ProjectionResult(cells=tuple(cells), gaps=tuple(gaps))


def _build_cell(
    slices: Sequence[SliceFit],
    *,
    market: SnapshotMarketState,
    tenor_label: str,
    maturity: float,
    discount_factor: float,
    forward: float,
    k: float,
    delta_band: str,
    target_delta: float,
    monetization: MonetizationConfig,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> ProjectedOptionAnalytics:
    """Price one solved (tenor, delta-band) cell and emit its stamped contract.

    The cell's option right follows the band label's side suffix
    (:func:`_option_right_for_band`): a ``…p`` band is a put, a ``…c`` band a call, and the two
    ATM pillars are the call (``atm``) and the put (``atmp``) at the one ATM-forward strike. It is
    priced at the IV read off the surface at the solved strike — so the IV used to price equals
    the IV the strike was solved against (no mismatch). The decimal Greeks are the engine's
    per-unit Greeks (source of truth); the
    dollar layer is derived once via :func:`pricing.dollar_greeks` with the configured
    flags and unit strings (one dollar-Greek home, no second code path).
    """
    strike = forward * math.exp(k)
    vol = _iv_at(slices, k, maturity)
    total_variance = vol * vol * maturity
    option_right = _option_right_for_band(delta_band, target_delta)
    state = from_forward(
        forward=forward, strike=strike, maturity_years=maturity, volatility=vol,
        discount_factor=discount_factor, option_right=option_right, spot=market.spot,
    )
    greeks = price_european(state)
    monetized = dollar_greeks(
        delta=greeks.delta, gamma=greeks.gamma, vega=greeks.vega, theta=greeks.theta,
        rho=greeks.rho, spot=market.spot, multiplier=1.0, quantity=1.0, config=monetization,
    )
    return ProjectedOptionAnalytics(
        snapshot_ts=snapshot_ts,
        provider=market.provider,
        underlying=market.underlying,
        tenor_label=tenor_label,
        maturity_years=maturity,
        delta_band=delta_band,
        target_delta=target_delta,
        log_moneyness=k,
        strike=strike,
        forward_price=forward,
        implied_vol=vol,
        total_variance=total_variance,
        price=greeks.price,
        delta=greeks.delta,
        gamma=greeks.gamma,
        vega=greeks.vega,
        theta=greeks.theta,
        rho=greeks.rho,
        dollar_delta=monetized.dollar_delta,
        dollar_gamma=monetized.dollar_gamma,
        dollar_vega=monetized.dollar_vega,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=monetized.gamma_unit,
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version=SURFACE_VERSION,
        pricer_version=PRICER_VERSION,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
        dollar_theta=monetized.dollar_theta,
        dollar_rho=monetized.dollar_rho,
        dollar_theta_unit=monetized.theta_unit,
        dollar_rho_unit=UNIT_STRINGS["dollar_rho"],
    )
