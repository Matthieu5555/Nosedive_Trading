from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from algotrading.core.config import (
    ConfigFieldError,
    MonetizationConfig,
    object_config_hash,
)
from algotrading.core.provenance import ProvenanceStamp, snapshot_stamp, source_ref
from algotrading.infra.contracts import SURFACE_SIDE_COMBINED, ProjectedOptionAnalytics
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

PROJECTION_VERSION = "projection-1.1.0"


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

class ProjectionConfigError(ConfigFieldError):
    pass


_DEFAULT_BAND_LOW_DELTA = -0.30
_DEFAULT_BAND_HIGH_DELTA = 0.30
_DEFAULT_BAND_STEP = 0.02


def delta_band_axis(
    *, band_low_delta: float, band_high_delta: float, band_step: float,
) -> tuple[tuple[str, ...], tuple[float, ...]]:
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
    for mag_c in range(-low_c, 0, -step_c):
        labels.append(f"{mag_c:02d}dp")
        targets.append(-mag_c / 100.0)
    labels.extend(("atm", "atmp"))
    targets.extend((0.0, 0.0))
    for mag_c in range(step_c, high_c + 1, step_c):
        labels.append(f"{mag_c:02d}dc")
        targets.append(mag_c / 100.0)
    return tuple(labels), tuple(targets)


def _centi_delta(value: float, field: str) -> int:
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

_DELTA_SOLVE_MAX_ITER = 100
_DELTA_SOLVE_TOL = 1e-10


@dataclass(frozen=True, slots=True)
class ProjectionConfig:

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
        band_labels, band_targets = delta_band_axis(
            band_low_delta=band_low_delta, band_high_delta=band_high_delta, band_step=band_step,
        )
        return cls(
            version=version, tenor_grid=tenor_grid, band_labels=band_labels,
            band_targets=band_targets, interpolation=interpolation, clamp_to_span=clamp_to_span,
        )


def tenor_years(label: str) -> float:
    try:
        return _TENOR_YEARS[label]
    except KeyError:
        raise ProjectionConfigError(
            "projection", "tenor_label", label, f"unknown tenor; must be one of {PINNED_TENORS}"
        ) from None


# How a pinned (projected) tenor was produced from the captured liquid maturities — the
# blueprint's Eq.-22 interior interpolation vs the 05-math-notes edge fallback, made explicit
# and auditable (ADR 0052). `direct`: the pin coincides with a captured liquid slice maturity.
# `interpolated`: the pin lies strictly inside the liquid span, filled by total-variance
# interpolation. `extrapolated`: the pin is below/above the liquid span — a low-confidence
# fallback, never a hard defect.
PROVENANCE_DIRECT = "direct"
PROVENANCE_INTERPOLATED = "interpolated"
PROVENANCE_EXTRAPOLATED = "extrapolated"

_MATURITY_MATCH_TOL = 1e-9


def classify_tenor_provenance(
    maturity_years: float,
    *,
    liquid_span: tuple[float, float] | None,
    direct_maturities: Sequence[float] = (),
) -> str:
    """Label a pinned tenor `direct | interpolated | extrapolated` against the liquid span.

    `liquid_span` is the (min, max) maturity of the captured liquid slices; `direct_maturities`
    are the maturities that carry a captured liquid slice. A pin outside the span is
    `extrapolated` (edge fallback); a pin matching a captured maturity is `direct`; any other
    pin inside the span is `interpolated` (Eq. 22).
    """
    if liquid_span is None:
        return PROVENANCE_EXTRAPOLATED
    low, high = liquid_span
    if maturity_years < low - _MATURITY_MATCH_TOL or maturity_years > high + _MATURITY_MATCH_TOL:
        return PROVENANCE_EXTRAPOLATED
    for direct in direct_maturities:
        if abs(direct - maturity_years) <= _MATURITY_MATCH_TOL:
            return PROVENANCE_DIRECT
    return PROVENANCE_INTERPOLATED


def tenor_provenance_map(
    slices: Sequence[SliceFit], tenor_grid: Sequence[str],
) -> dict[str, str]:
    """Per-pinned-tenor provenance label, keyed by tenor label (ADR 0052)."""
    span = _usable_span(slices)
    direct = tuple(
        s.maturity_years for s in slices if s.method != METHOD_INSUFFICIENT
    )
    return {
        label: classify_tenor_provenance(
            tenor_years(label), liquid_span=span, direct_maturities=direct
        )
        for label in tenor_grid
    }


@dataclass(frozen=True, slots=True)
class ProjectionGap:

    underlying: str
    tenor_label: str
    delta_band: str
    target_delta: float
    reason_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ProjectionResult:

    cells: tuple[ProjectedOptionAnalytics, ...]
    gaps: tuple[ProjectionGap, ...]
    # Per-pinned-tenor provenance label (`direct | interpolated | extrapolated`, ADR 0052),
    # so QC and the front can read how each pin was produced. Empty for legacy callers.
    tenor_provenance: Mapping[str, str] = field(default_factory=dict)


def _usable_span(slices: Sequence[SliceFit]) -> tuple[float, float] | None:
    usable = [s.maturity_years for s in slices if s.method != METHOD_INSUFFICIENT]
    if not usable:
        return None
    return min(usable), max(usable)


def _strike_span(slices: Sequence[SliceFit]) -> tuple[float, float] | None:
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
    w = max(interpolate_total_variance(slices, k, maturity_years), 0.0)
    return math.sqrt(w / maturity_years) if maturity_years > 0.0 else 0.0


def _call_nd1(*, forward: float, strike: float, maturity_years: float,
              volatility: float, discount_factor: float) -> float:
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
    target_nd1 = 0.5 if target_delta == 0.0 else (
        target_delta if target_delta > 0.0 else 1.0 + target_delta
    )
    low_k, high_k = span

    def nd1_at(k: float) -> float:
        strike = forward * math.exp(k)
        vol = _iv_at(slices, k, maturity_years)
        if vol <= 0.0:
            return math.nan
        return _call_nd1(
            forward=forward, strike=strike, maturity_years=maturity_years,
            volatility=vol, discount_factor=discount_factor,
        )

    nd1_low, nd1_high = nd1_at(low_k), nd1_at(high_k)
    if math.isnan(nd1_low) or math.isnan(nd1_high):
        return None
    hi_val, lo_val = nd1_low, nd1_high
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


def option_right_for_band(label: str) -> str:
    """Canonical map from a delta-band display label to its option right ("C"|"P").

    This is the single source of truth shared with the BFF (which imports it instead of
    keeping its own copy). The at-the-money-forward pillar is emitted twice, once as a call
    ("atm") and once as a put ("atmp"), so it can show a delta-neutral straddle; everything
    else is a put if the label ends in "p" and a call if it ends in "c".
    """
    if label in ("atm", "atmf"):
        return "C"
    if label == "atmp":
        return "P"
    if label.endswith("p"):
        return "P"
    if label.endswith("c"):
        return "C"
    raise ProjectionConfigError(
        "projection", "delta_band", label,
        "delta-band label must be 'atm', 'atmf', 'atmp', or end in 'c'/'p'",
    )


def _option_right_for_band(delta_band: str, target_delta: float) -> str:
    # Internal solved-strike path knows the target sign already; defer to the canonical
    # resolver for the label vocabulary, fall back to the sign for any unsuffixed label.
    if delta_band in ("atm", "atmp") or delta_band.endswith(("p", "c")):
        return option_right_for_band(delta_band)
    return "P" if target_delta < 0.0 else "C"


def _projection_stamp(
    slices: Sequence[SliceFit],
    *,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> ProvenanceStamp:
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
    merged = dict(base)
    merged["projection"] = projection.config_hash()
    merged.setdefault("scenarios", object_config_hash(monetization))
    return merged


def _side_iv(slices: Sequence[SliceFit], k: float, maturity_years: float) -> float | None:
    try:
        return _iv_at(slices, k, maturity_years)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ListedContract:
    """A real exchange-listed option contract to project the fitted surface onto.

    The price table is built one row per listed contract (D1) instead of at synthetic
    `forward*exp(k)` strikes, so the BFF can attach a captured quote by the exact
    `(tenor_label, right, strike)` key — no fuzzy nearest-strike match. `strike` is the
    exchange's round listed strike (e.g. 1480, 1520); `right` is "C" or "P".
    """

    tenor_label: str
    maturity_years: float
    right: str
    strike: float


_PARITY_ABS_TOL = 1e-6
_PARITY_REL_TOL = 1e-9


class ProjectionParityError(ProjectionConfigError):
    def __init__(
        self, underlying: str, tenor_label: str, delta_band: str, surface_side: str, breach: float
    ) -> None:
        super().__init__(
            "projection",
            "put_call_parity",
            breach,
            (
                f"{underlying} {tenor_label} {delta_band} ({surface_side}): the call and put marks "
                f"at this strike/expiry violate put-call parity by {breach:.6g} (must price off one "
                "implied vol; a per-side vol gap manufactures arbitrage on the surface)"
            ),
        )


def parity_breach(
    cell: ProjectedOptionAnalytics, *, discount_factor: float, forward: float
) -> float | None:
    """Absolute put-call-parity residual of a projected cell, or None when within tolerance.

    A European call and put at one strike/expiry must satisfy C - P = DF * (forward - strike),
    independent of vol. `cell.price` and `cell.price_mirror` are the two rights priced off the
    cell's single implied vol, so this residual is the small numerical parity term unless two
    different vols leaked into the same strike.
    """
    option_right = _option_right_for_band(cell.delta_band, cell.target_delta)
    if option_right == "C":
        call_price, put_price = cell.price, cell.price_mirror
    else:
        call_price, put_price = cell.price_mirror, cell.price
    expected = discount_factor * (forward - cell.strike)
    residual = abs((call_price - put_price) - expected)
    scale = abs(expected) + discount_factor * forward
    if residual <= _PARITY_ABS_TOL + _PARITY_REL_TOL * scale:
        return None
    return residual


def _band_label_for_listed_delta(
    *, call_nd1: float, option_right: str, log_moneyness: float, band_step_centi: int = 2,
) -> str:
    """Display/grouping label for a listed contract, from its OWN model delta magnitude.

    Keeps the existing band vocabulary ("..dc" for calls, "..dp" for puts, "atmf" at the
    forward) so the price table groups rows the same way the delta-band grid does. The label
    follows the contract's right, not the moneyness: a call gets its call-delta hundredth
    (N(d1)), a put gets its put-delta magnitude (1 - N(d1)); both snapped to the band step so
    they land on the same grid as the pinned bands. At the forward (k ≈ 0) the row is the
    honest at-the-money-forward pillar (D5).
    """
    if not math.isfinite(call_nd1):
        return "off"
    if abs(log_moneyness) < 1e-9:
        # At the forward a call and a put share the strike, so they must carry distinct labels
        # or they collide on the projected primary key. Follow the atm/atmp convention: the call
        # pillar is "atmf", the put pillar "atmfp".
        return "atmf" if option_right == "C" else "atmfp"
    if option_right == "C":
        mag = call_nd1  # call delta, in (0, 1)
        suffix = "dc"
    else:
        mag = 1.0 - call_nd1  # |put delta| = 1 - N(d1)
        suffix = "dp"
    centi = int(round(mag * 100.0 / band_step_centi)) * band_step_centi
    centi = max(band_step_centi, min(centi, 98))
    return f"{centi:02d}{suffix}"


def project_grid(
    slices: Sequence[SliceFit],
    market: SnapshotMarketState,
    *,
    put_slices: Sequence[SliceFit] = (),
    call_slices: Sequence[SliceFit] = (),
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    projection: ProjectionConfig,
    monetization: MonetizationConfig,
    config_hashes: Mapping[str, str],
    listed_contracts: Sequence[ListedContract] | None = None,
) -> ProjectionResult:
    """Project the fitted surface onto a grid of priced cells.

    Two emission modes:

    * `listed_contracts` is given (D1) -> one cell per real exchange-listed contract, keyed by
      its listed (tenor, right, strike). The BFF attaches a captured quote by that exact key.
    * `listed_contracts` is None -> the legacy delta-band grid at solved synthetic strikes.

    `forwards` (D4) is the captured per-maturity forward curve (maturity-years -> forward).
    When supplied it replaces `market.spot` as the forward; a maturity with no forward is a
    labeled `no_forward` gap, never a silent spot substitution. When None, the forward is
    `market.spot` (legacy path; callers where spot==forward by construction).
    """
    if listed_contracts is not None:
        return _project_listed_grid(
            slices, market,
            put_slices=put_slices, call_slices=call_slices,
            snapshot_ts=snapshot_ts, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts,
            projection=projection, monetization=monetization, config_hashes=config_hashes,
            listed_contracts=listed_contracts,
        )
    full_hashes = merged_config_hashes(
        config_hashes, projection=projection, monetization=monetization
    )
    provenance = _projection_stamp(
        slices, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts, config_hashes=full_hashes
    )
    span = _usable_span(slices)
    strike_span = _strike_span(slices)

    side_sets: list[tuple[str, Sequence[SliceFit]]] = [(SURFACE_SIDE_COMBINED, slices)]
    if put_slices:
        side_sets.append(("put", put_slices))
    if call_slices:
        side_sets.append(("call", call_slices))

    cells: list[ProjectedOptionAnalytics] = []
    gaps: list[ProjectionGap] = []
    for tenor_label in projection.tenor_grid:
        maturity = tenor_years(tenor_label)
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
        forward = market.forward_for(tenor_label, maturity)
        if forward is None:
            for label, target in zip(projection.band_labels, projection.band_targets, strict=True):
                gaps.append(ProjectionGap(
                    underlying=market.underlying, tenor_label=tenor_label, delta_band=label,
                    target_delta=target, reason_code="no_forward",
                    detail=(
                        f"{market.underlying} {tenor_label} (T={maturity:.4f}y): the captured "
                        "forward curve has no forward for this maturity — labeled gap, no spot "
                        "substitution"
                    ),
                ))
            continue
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
            for surface_side, side_slices in side_sets:
                vol = _side_iv(side_slices, k, maturity)
                if vol is None:
                    gaps.append(ProjectionGap(
                        underlying=market.underlying, tenor_label=tenor_label, delta_band=label,
                        target_delta=target, reason_code="side_no_curve",
                        detail=(
                            f"{market.underlying} {tenor_label} {label}: the {surface_side} "
                            "surface has no fitted curve at this maturity — labeled gap, no guess"
                        ),
                    ))
                    continue
                cell = _build_cell(
                    market=market, tenor_label=tenor_label, maturity=maturity,
                    discount_factor=discount_factor, forward=forward, k=k, vol=vol,
                    surface_side=surface_side, delta_band=label, target_delta=target,
                    monetization=monetization, snapshot_ts=snapshot_ts,
                    source_snapshot_ts=source_snapshot_ts, provenance=provenance,
                )
                breach = parity_breach(cell, discount_factor=discount_factor, forward=forward)
                if breach is not None:
                    raise ProjectionParityError(
                        market.underlying, tenor_label, label, surface_side, breach
                    )
                cells.append(cell)

    provenance_map = tenor_provenance_map(slices, projection.tenor_grid)
    return ProjectionResult(
        cells=tuple(cells), gaps=tuple(gaps), tenor_provenance=provenance_map
    )


def _call_nd1_at_strike(
    slices: Sequence[SliceFit],
    *,
    forward: float,
    strike: float,
    maturity_years: float,
    discount_factor: float,
) -> tuple[float, float]:
    """Model N(d1) (call delta / DF) and log-moneyness for a listed strike. NaN nd1 if no vol."""
    k = math.log(strike / forward)
    vol = _iv_at(slices, k, maturity_years)
    if vol <= 0.0:
        return math.nan, k
    return _call_nd1(
        forward=forward, strike=strike, maturity_years=maturity_years,
        volatility=vol, discount_factor=discount_factor,
    ), k


def _project_listed_grid(
    slices: Sequence[SliceFit],
    market: SnapshotMarketState,
    *,
    put_slices: Sequence[SliceFit],
    call_slices: Sequence[SliceFit],
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    projection: ProjectionConfig,
    monetization: MonetizationConfig,
    config_hashes: Mapping[str, str],
    listed_contracts: Sequence[ListedContract],
) -> ProjectionResult:
    """Emit one priced cell per real listed contract (D1), keyed by (tenor, right, strike).

    The theoretical columns (price, iv, greeks) come from evaluating the FITTED surface at the
    listed strike and the corrected forward. `delta_band` is a display/grouping label derived
    from the MODEL delta at that strike — it is NOT used to choose the strike.
    """
    full_hashes = merged_config_hashes(
        config_hashes, projection=projection, monetization=monetization
    )
    provenance = _projection_stamp(
        slices, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts, config_hashes=full_hashes
    )
    span = _usable_span(slices)

    side_sets: list[tuple[str, Sequence[SliceFit]]] = [(SURFACE_SIDE_COMBINED, slices)]
    if put_slices:
        side_sets.append(("put", put_slices))
    if call_slices:
        side_sets.append(("call", call_slices))

    cells: list[ProjectedOptionAnalytics] = []
    gaps: list[ProjectionGap] = []

    ordered = sorted(
        listed_contracts,
        key=lambda c: (c.maturity_years, c.strike, 0 if c.right == "P" else 1),
    )
    for contract in ordered:
        tenor_label = contract.tenor_label
        maturity = contract.maturity_years
        band = f"{contract.strike:g}{contract.right}"  # provisional, refined once priced
        if span is None:
            gaps.append(ProjectionGap(
                underlying=market.underlying, tenor_label=tenor_label, delta_band=band,
                target_delta=0.0, reason_code="no_curve",
                detail=f"{market.underlying} {tenor_label}: no fitted slice carries a curve",
            ))
            continue
        low_m, high_m = span
        if not projection.clamp_to_span and not (low_m <= maturity <= high_m):
            gaps.append(ProjectionGap(
                underlying=market.underlying, tenor_label=tenor_label, delta_band=band,
                target_delta=0.0, reason_code="tenor_beyond_span",
                detail=(
                    f"{market.underlying} {tenor_label} (T={maturity:.4f}y) is outside the "
                    f"fitted span [{low_m:.4f}, {high_m:.4f}]y — no extrapolation"
                ),
            ))
            continue
        forward = market.forward_for(tenor_label, maturity)
        if forward is None:
            gaps.append(ProjectionGap(
                underlying=market.underlying, tenor_label=tenor_label, delta_band=band,
                target_delta=0.0, reason_code="no_forward",
                detail=(
                    f"{market.underlying} {tenor_label} (T={maturity:.4f}y): the captured "
                    "forward curve has no forward for this maturity — labeled gap, no spot "
                    "substitution"
                ),
            ))
            continue
        discount_factor = market.discount_factor_for(tenor_label, maturity)
        call_nd1, k = _call_nd1_at_strike(
            slices, forward=forward, strike=contract.strike,
            maturity_years=maturity, discount_factor=discount_factor,
        )
        delta_band = _band_label_for_listed_delta(
            call_nd1=call_nd1, option_right=contract.right, log_moneyness=k,
        )
        # The display delta sign for a put is negative; signal it to consumers via target_delta.
        target_delta = (
            (call_nd1 if contract.right == "C" else call_nd1 - 1.0)
            if math.isfinite(call_nd1) else 0.0
        )
        emitted = False
        for surface_side, side_slices in side_sets:
            vol = _side_iv(side_slices, k, maturity)
            if vol is None:
                gaps.append(ProjectionGap(
                    underlying=market.underlying, tenor_label=tenor_label, delta_band=delta_band,
                    target_delta=target_delta, reason_code="side_no_curve",
                    detail=(
                        f"{market.underlying} {tenor_label} K={contract.strike:g}: the "
                        f"{surface_side} surface has no fitted curve at this maturity"
                    ),
                ))
                continue
            cell = _build_listed_cell(
                market=market, tenor_label=tenor_label, maturity=maturity,
                discount_factor=discount_factor, forward=forward, strike=contract.strike,
                log_moneyness=k, vol=vol, option_right=contract.right,
                surface_side=surface_side, delta_band=delta_band, target_delta=target_delta,
                monetization=monetization, snapshot_ts=snapshot_ts,
                source_snapshot_ts=source_snapshot_ts, provenance=provenance,
            )
            breach = parity_breach(cell, discount_factor=discount_factor, forward=forward)
            if breach is not None:
                raise ProjectionParityError(
                    market.underlying, tenor_label, delta_band, surface_side, breach
                )
            cells.append(cell)
            emitted = True
        if not emitted:
            continue

    provenance_map = tenor_provenance_map(slices, projection.tenor_grid)
    return ProjectionResult(
        cells=tuple(cells), gaps=tuple(gaps), tenor_provenance=provenance_map
    )


def _mirror_right(option_right: str) -> str:
    return "P" if option_right == "C" else "C"


def _build_cell(
    *,
    market: SnapshotMarketState,
    tenor_label: str,
    maturity: float,
    discount_factor: float,
    forward: float,
    k: float,
    vol: float,
    surface_side: str,
    delta_band: str,
    target_delta: float,
    monetization: MonetizationConfig,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> ProjectedOptionAnalytics:
    strike = forward * math.exp(k)
    option_right = _option_right_for_band(delta_band, target_delta)
    return _build_listed_cell(
        market=market, tenor_label=tenor_label, maturity=maturity,
        discount_factor=discount_factor, forward=forward, strike=strike,
        log_moneyness=k, vol=vol, option_right=option_right,
        surface_side=surface_side, delta_band=delta_band, target_delta=target_delta,
        monetization=monetization, snapshot_ts=snapshot_ts,
        source_snapshot_ts=source_snapshot_ts, provenance=provenance,
    )


def _build_listed_cell(
    *,
    market: SnapshotMarketState,
    tenor_label: str,
    maturity: float,
    discount_factor: float,
    forward: float,
    strike: float,
    log_moneyness: float,
    vol: float,
    option_right: str,
    surface_side: str,
    delta_band: str,
    target_delta: float,
    monetization: MonetizationConfig,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> ProjectedOptionAnalytics:
    k = log_moneyness
    total_variance = vol * vol * maturity
    state = from_forward(
        forward=forward, strike=strike, maturity_years=maturity, volatility=vol,
        discount_factor=discount_factor, option_right=option_right, spot=market.spot,
    )
    greeks = price_european(state)
    monetized = dollar_greeks(
        delta=greeks.delta, gamma=greeks.gamma, vega=greeks.vega, theta=greeks.theta,
        rho=greeks.rho, spot=market.spot, rt_vega=greeks.rt_vega,
        vanna=greeks.vanna, volga=greeks.volga, charm=greeks.charm,
        multiplier=1.0, quantity=1.0, config=monetization,
    )

    mirror_right = _mirror_right(option_right)
    mirror_state = from_forward(
        forward=forward, strike=strike, maturity_years=maturity, volatility=vol,
        discount_factor=discount_factor, option_right=mirror_right, spot=market.spot,
    )
    mirror_greeks = price_european(mirror_state)
    mirror_monetized = dollar_greeks(
        delta=mirror_greeks.delta, gamma=mirror_greeks.gamma, vega=mirror_greeks.vega,
        theta=mirror_greeks.theta, rho=mirror_greeks.rho,
        spot=market.spot, multiplier=1.0, quantity=1.0, config=monetization,
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
        rt_vega=greeks.rt_vega,
        dollar_rt_vega=monetized.dollar_rt_vega,
        dollar_rt_vega_unit=UNIT_STRINGS["dollar_rt_vega"],
        surface_side=surface_side,
        price_mirror=mirror_greeks.price,
        delta_mirror=mirror_greeks.delta,
        theta_mirror=mirror_greeks.theta,
        rho_mirror=mirror_greeks.rho,
        dollar_delta_mirror=mirror_monetized.dollar_delta,
        dollar_theta_mirror=mirror_monetized.dollar_theta,
        dollar_rho_mirror=mirror_monetized.dollar_rho,
        vanna=greeks.vanna,
        volga=greeks.volga,
        charm=greeks.charm,
        dollar_vanna=monetized.dollar_vanna,
        dollar_volga=monetized.dollar_volga,
        dollar_charm=monetized.dollar_charm,
        dollar_vanna_unit=UNIT_STRINGS["dollar_vanna"],
        dollar_volga_unit=UNIT_STRINGS["dollar_volga"],
        dollar_charm_unit=monetized.charm_unit,
    )


@dataclass(frozen=True, slots=True)
class IvSpreadPoint:

    provider: str
    underlying: str
    tenor_label: str
    delta_band: str
    strike: float
    put_iv: float
    call_iv: float
    iv_spread: float


def put_call_iv_spread(
    cells: Iterable[ProjectedOptionAnalytics],
) -> tuple[IvSpreadPoint, ...]:
    puts: dict[tuple[str, str, str, str], ProjectedOptionAnalytics] = {}
    calls: dict[tuple[str, str, str, str], ProjectedOptionAnalytics] = {}
    for cell in cells:
        key = (cell.provider, cell.underlying, cell.tenor_label, cell.delta_band)
        if cell.surface_side == "put":
            puts[key] = cell
        elif cell.surface_side == "call":
            calls[key] = cell

    out: list[IvSpreadPoint] = []
    for key in puts.keys() & calls.keys():
        put_cell, call_cell = puts[key], calls[key]
        out.append(IvSpreadPoint(
            provider=put_cell.provider,
            underlying=put_cell.underlying,
            tenor_label=put_cell.tenor_label,
            delta_band=put_cell.delta_band,
            strike=put_cell.strike,
            put_iv=put_cell.implied_vol,
            call_iv=call_cell.implied_vol,
            iv_spread=put_cell.implied_vol - call_cell.implied_vol,
        ))
    out.sort(key=lambda p: (p.underlying, p.tenor_label, p.delta_band))
    return tuple(out)
