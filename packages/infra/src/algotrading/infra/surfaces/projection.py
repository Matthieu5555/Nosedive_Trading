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

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from algotrading.core.config import ConfigFieldError, MonetizationConfig, canonical_json
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.contracts import ProjectedOptionAnalytics
from algotrading.infra.pricing import (
    UNIT_STRINGS,
    dollar_greeks,
    from_forward,
    price_european,
)

from .fit import METHOD_INSUFFICIENT, SliceFit, interpolate_total_variance
from .svi import SURFACE_VERSION

# Bump only on a real change to the projection logic, never on config.
# 1.1.0: discount factors interpolated from the listed-expiry curve at the pinned tenor
# (flat-forward in -ln DF) instead of the silent rate-free fallback on a float-key miss
# (F-SURF-01).
PROJECTION_VERSION = "projection-1.1.0"

# The pricer version this grid prices with — closed-form Black-76 European leg. Mirrors
# pricing.engine.PRICER_VERSION; named here so a cell records the engine that produced it.
PRICER_VERSION = "black76-lr-1.0.0"

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

# The default delta-band axis: the signed target deltas spanning the 30Δ put through ATM to
# the 30Δ call (blueprint OQ-4 default window). A put target is negative, a call positive,
# ATM is the 0.50 call-delta pillar (signed 0.0 here as the band centre label). The labels
# are the band names the front renders; the magnitude is the |delta| solved for.
#
# ``atm`` and ``atmp`` are the call and the put at the SAME ATM-forward strike (both solve from
# target 0.0): the call (``atm``) and the put (``atmp``) that compose an ATM straddle. The option
# right is taken from the label suffix (``…p`` → put, ``…c`` → call), so the two ATM pillars are
# a call and a put at one strike — see :func:`_option_right_for_band`. A straddle is the two of
# them summed (delta-neutral, 2× gamma/vega); without ``atmp`` the grid could not express it.
_DEFAULT_BAND_LABELS: tuple[str, ...] = (
    "30dp", "20dp", "10dp", "atm", "atmp", "10dc", "20dc", "30dc",
)
_DEFAULT_BAND_TARGETS: tuple[float, ...] = (
    -0.30, -0.20, -0.10, 0.0, 0.0, 0.10, 0.20, 0.30,
)

# Newton/bisection budget and tolerance for the delta -> strike inversion. Numerical
# invariants (how hard we try, how close is "solved"), not economic tunables, so they stay
# code constants per the config standard's invariant carve-out.
_DELTA_SOLVE_MAX_ITER = 100
_DELTA_SOLVE_TOL = 1e-10


class ProjectionConfigError(ConfigFieldError):
    """A projection-config field was malformed."""


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

        Reuses :func:`~algotrading.core.config.canonical_json`, so ``-0.0`` collapses onto
        ``0.0`` and a NaN/Inf is rejected — the hash is byte-identical across processes
        without ``PYTHONHASHSEED`` (the C7 hardening the golden test pins).
        """
        import hashlib

        return hashlib.sha256(canonical_json(self).encode("utf-8")).hexdigest()


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


@dataclass(frozen=True, slots=True)
class SnapshotMarketState:
    """The per-underlying market state a projection prices against, at one snapshot.

    ``spot`` is the underlying reference spot. The discount curve comes in two shapes:
    ``discount_factors_by_tenor`` keyed by **pinned tenor label** (the join that matches by
    construction — preferred when the capture lane provides it), and ``discount_factors``
    keyed by **maturity in years** (the listed-expiry knots the forward estimates priced).
    Carry is taken as zero (the spot==forward, Black-76/futures view) so spot and forward
    delta coincide, matching the delta-band inversion convention; the forward at a tenor is
    then ``spot * discount-free`` — i.e. the forward equals spot here, and the discount
    factor only scales the option price. This is the same ``carry == 0`` pin the 1B
    delta-band selection uses.
    """

    underlying: str
    provider: str
    spot: float
    discount_factors: Mapping[float, float] = field(default_factory=dict)
    default_discount_factor: float = 1.0
    discount_factors_by_tenor: Mapping[str, float] = field(default_factory=dict)

    def discount_factor_for(self, tenor_label: str, maturity_years: float) -> float:
        """The discount factor for a pinned-tenor cell: label binding first, then the curve.

        A ``discount_factors_by_tenor`` entry wins outright — the tenor label is the one
        join key that cannot drift through float re-derivation (F-SURF-01). Without one,
        the factor is read off the maturity-keyed curve via :meth:`discount_factor_at`.
        """
        by_tenor = self.discount_factors_by_tenor.get(tenor_label)
        if by_tenor is not None:
            return by_tenor
        return self.discount_factor_at(maturity_years)

    def discount_factor_at(self, maturity_years: float) -> float:
        """The discount factor at ``maturity_years``, read off the snapshot's DF curve.

        The curve knots are the *listed-expiry* maturities the forward estimates priced,
        while the projection queries the *pinned-tenor* years — the two grids rarely
        coincide, so an exact dict hit cannot be relied on (F-SURF-01: the old exact
        ``get`` silently priced every cell rate-free). Resolution order:

        * an exact key hit returns the stored factor unchanged (bit-for-bit, no log/exp
          round-trip);
        * between knots, the total log-discount ``-ln DF`` is interpolated linearly in
          maturity (flat-forward, the standard curve rule; exact for a flat zero rate);
        * beyond the knot span — and for a single-knot curve — the nearest knot's zero
          rate is held flat, ``DF(T) = exp(-r_nearest · T)``, so ``DF(0) → 1`` rather
          than freezing a long-dated factor onto a short tenor;
        * an **empty** curve falls back to ``default_discount_factor`` — the documented,
          explicitly injected no-curve degradation (the replay paths rely on it), not a
          silent key-miss.
        """
        exact = self.discount_factors.get(maturity_years)
        if exact is not None:
            return exact
        knots = sorted(
            (t, df)
            for t, df in self.discount_factors.items()
            if math.isfinite(t) and math.isfinite(df) and t > 0.0 and df > 0.0
        )
        if not knots:
            return self.default_discount_factor
        times = [t for t, _ in knots]
        log_discounts = [-math.log(df) for _, df in knots]
        if maturity_years <= times[0]:
            return math.exp(-(log_discounts[0] / times[0]) * maturity_years)
        if maturity_years >= times[-1]:
            return math.exp(-(log_discounts[-1] / times[-1]) * maturity_years)
        index = bisect.bisect_left(times, maturity_years)
        span = times[index] - times[index - 1]
        weight = (maturity_years - times[index - 1]) / span
        interpolated = log_discounts[index - 1] + weight * (
            log_discounts[index] - log_discounts[index - 1]
        )
        return math.exp(-interpolated)


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
    return stamp(
        calc_ts=calc_ts,
        code_version=PROJECTION_VERSION,
        config_hashes=config_hashes,
        source_records=refs,
        source_timestamps=tuple(source_snapshot_ts for _ in refs),
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
    import hashlib

    merged.setdefault(
        "scenarios",
        hashlib.sha256(canonical_json(monetization).encode("utf-8")).hexdigest(),
    )
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
