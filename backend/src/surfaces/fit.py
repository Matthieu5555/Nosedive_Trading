"""Fit one maturity slice, interpolate across maturities, and emit the contracts.

The orchestration of step 9. :func:`fit_slice` takes the solved :class:`IvPoint`
records for one underlying and maturity and returns a rich :class:`SliceFit`:

* with enough distinct strikes, a calibrated SVI smile (:mod:`surfaces.svi`) plus
  its butterfly no-arbitrage check and bound-hit flags;
* with too few, a *labeled* nonparametric fallback — linear interpolation of the
  observed total variance — so a sparse slice still produces a usable curve and is
  never silently presented as a calibrated model;
* with none, a labeled ``insufficient`` slice that yields nothing.

The raw points are retained on the fit (never discarded after calibration), the fit
can be sampled for plotting raw-vs-fitted, and the usable part projects into A's
``SurfaceParameters`` (SVI only) and ``SurfaceGrid`` (any method) contracts.
Cross-maturity total variance (Eq 22) interpolates linearly in ``w`` between the
bracketing slices. Pure throughout: ``calc_ts`` is injected at emission.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from contracts import (
    IvPoint,
    SurfaceFitDiagnostics,
    SurfaceGrid,
    SurfaceParameters,
)
from provenance import ProvenanceStamp, source_ref, stamp

from .arbitrage import butterfly_violations
from .svi import MIN_POINTS_FOR_SVI, SURFACE_VERSION, SviFit, SviParams, fit_svi

# Method labels: every slice says how its curve was built, never implied.
METHOD_SVI = "svi"
METHOD_NONPARAMETRIC = "nonparametric"
METHOD_INSUFFICIENT = "insufficient"

# Padding (in log-moneyness) beyond the observed strikes for the butterfly grid, and
# the number of grid points, so the no-arb check probes a little past the data.
_ARB_GRID_PAD = 0.1
_ARB_GRID_POINTS = 21


@dataclass(frozen=True, slots=True)
class SliceFit:
    """One maturity slice's calibration, diagnostics, and retained raw points.

    ``svi`` is set only when ``method == "svi"``. For the nonparametric fallback,
    ``nonparametric_ks``/``nonparametric_ws`` hold the sorted observed points used
    for interpolation. ``arb_free`` is the butterfly verdict for an SVI slice and a
    positivity check for a nonparametric one. ``raw_points`` are the solved IvPoints,
    kept so the fit is always auditable against its inputs.
    """

    underlying: str
    maturity_years: float
    expiry_date: date
    day_count: str
    method: str
    svi: SviParams | None
    rmse: float
    n_points: int
    arb_free: bool
    bound_hits: tuple[str, ...]
    butterfly_violations: tuple[float, ...]
    nonparametric_ks: tuple[float, ...]
    nonparametric_ws: tuple[float, ...]
    raw_points: tuple[IvPoint, ...]

    def total_variance(self, k: float) -> float:
        """Total variance at log-moneyness ``k`` from whichever model was fit.

        Raises ``ValueError`` for an ``insufficient`` slice, which has no curve.
        """
        if self.method == METHOD_SVI and self.svi is not None:
            return self.svi.total_variance(k)
        if self.method == METHOD_NONPARAMETRIC:
            return _interpolate_sorted(self.nonparametric_ks, self.nonparametric_ws, k)
        raise ValueError(f"slice for {self.underlying} has no curve ({self.method})")


def _interpolate_sorted(ks: tuple[float, ...], ws: tuple[float, ...], k: float) -> float:
    """Linear interpolation of ``w`` over sorted ``ks``, flat beyond the ends."""
    if k <= ks[0]:
        return ws[0]
    if k >= ks[-1]:
        return ws[-1]
    for index in range(1, len(ks)):
        if k <= ks[index]:
            span = ks[index] - ks[index - 1]
            weight = (k - ks[index - 1]) / span
            return ws[index - 1] + weight * (ws[index] - ws[index - 1])
    return ws[-1]  # pragma: no cover - the k >= ks[-1] guard already covers this


def _distinct_sorted_points(
    points: tuple[IvPoint, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Sorted, strike-deduplicated ``(k, total_variance)`` from usable IvPoints.

    Keeps only finite, non-negative total variances; on a duplicate ``k`` the first
    seen wins. Returned sorted by ``k`` so interpolation and the arb grid are ordered.
    """
    by_k: dict[float, float] = {}
    for point in points:
        if point.total_variance >= 0.0 and point.k not in by_k:
            by_k[point.k] = point.total_variance
    ks = tuple(sorted(by_k))
    return ks, tuple(by_k[k] for k in ks)


def _arb_grid(ks: tuple[float, ...]) -> tuple[float, ...]:
    """A log-moneyness grid spanning the observed strikes, padded a little."""
    low, high = ks[0] - _ARB_GRID_PAD, ks[-1] + _ARB_GRID_PAD
    step = (high - low) / (_ARB_GRID_POINTS - 1)
    return tuple(low + step * i for i in range(_ARB_GRID_POINTS))


def fit_slice(
    underlying: str,
    maturity_years: float,
    points: tuple[IvPoint, ...],
    *,
    expiry_date: date,
    day_count: str,
) -> SliceFit:
    """Fit one maturity slice, choosing SVI or a labeled nonparametric fallback.

    Total and pure: an empty slice returns a labeled ``insufficient`` fit rather than
    raising. With at least :data:`~surfaces.svi.MIN_POINTS_FOR_SVI` distinct strikes
    it calibrates SVI and runs the butterfly check; with fewer it falls back to
    interpolation, labeled ``nonparametric``.
    """
    ks, ws = _distinct_sorted_points(points)

    if len(ks) >= MIN_POINTS_FOR_SVI:
        svi_fit: SviFit = fit_svi(ks, ws)
        breaches = butterfly_violations(svi_fit.params, _arb_grid(ks))
        return SliceFit(
            underlying=underlying, maturity_years=maturity_years, expiry_date=expiry_date,
            day_count=day_count, method=METHOD_SVI, svi=svi_fit.params, rmse=svi_fit.rmse,
            n_points=svi_fit.n_points, arb_free=not breaches, bound_hits=svi_fit.bound_hits,
            butterfly_violations=breaches, nonparametric_ks=ks, nonparametric_ws=ws,
            raw_points=points,
        )

    if len(ks) >= 1:
        return SliceFit(
            underlying=underlying, maturity_years=maturity_years, expiry_date=expiry_date,
            day_count=day_count, method=METHOD_NONPARAMETRIC, svi=None, rmse=0.0,
            n_points=len(ks), arb_free=all(w > 0.0 for w in ws), bound_hits=(),
            butterfly_violations=(), nonparametric_ks=ks, nonparametric_ws=ws,
            raw_points=points,
        )

    return SliceFit(
        underlying=underlying, maturity_years=maturity_years, expiry_date=expiry_date,
        day_count=day_count, method=METHOD_INSUFFICIENT, svi=None, rmse=0.0, n_points=0,
        arb_free=True, bound_hits=(), butterfly_violations=(), nonparametric_ks=(),
        nonparametric_ws=(), raw_points=points,
    )


def interpolate_total_variance(
    slices: Sequence[SliceFit], k: float, maturity_years: float
) -> float:
    """Total variance at ``(k, maturity)`` across slices, linear in ``w`` (Eq 22).

    Uses only slices that carry a curve (SVI or nonparametric), sorted by maturity.
    Outside the maturity range it holds the nearest slice flat; inside it interpolates
    linearly in total variance between the two bracketing maturities — the standard
    calendar-consistent interpolation. Raises ``ValueError`` if no slice has a curve.
    """
    usable = sorted(
        (s for s in slices if s.method != METHOD_INSUFFICIENT), key=lambda s: s.maturity_years
    )
    if not usable:
        raise ValueError("no slice with a curve to interpolate")
    if maturity_years <= usable[0].maturity_years:
        return usable[0].total_variance(k)
    if maturity_years >= usable[-1].maturity_years:
        return usable[-1].total_variance(k)
    for lower, upper in zip(usable, usable[1:], strict=False):
        if lower.maturity_years <= maturity_years <= upper.maturity_years:
            span = upper.maturity_years - lower.maturity_years
            weight = (maturity_years - lower.maturity_years) / span
            w_low, w_high = lower.total_variance(k), upper.total_variance(k)
            return w_low + weight * (w_high - w_low)
    raise ValueError("maturity not bracketed")  # pragma: no cover - guarded by the range checks


@dataclass(frozen=True, slots=True)
class SlicePlotSeries:
    """Raw points and the fitted curve sampled on a grid, ready to plot.

    The plotting utility: hand ``raw_k``/``raw_w`` and ``grid_k``/``fitted_w`` to any
    plotting library to show the observed implied-vol points against the fitted
    slice. A good fit has the fitted curve passing near every raw point.
    """

    raw_k: tuple[float, ...]
    raw_w: tuple[float, ...]
    grid_k: tuple[float, ...]
    fitted_w: tuple[float, ...]


def slice_plot_series(fit: SliceFit, *, n_grid: int = 50) -> SlicePlotSeries:
    """Sample a slice's raw points and fitted curve for a raw-vs-fitted plot.

    Raises ``ValueError`` for an ``insufficient`` slice (nothing to show).
    """
    if fit.method == METHOD_INSUFFICIENT or not fit.nonparametric_ks:
        raise ValueError(f"nothing to plot for an {fit.method} slice")
    raw_k = fit.nonparametric_ks
    raw_w = fit.nonparametric_ws
    low, high = raw_k[0], raw_k[-1]
    step = (high - low) / (n_grid - 1) if n_grid > 1 and high > low else 0.0
    grid_k = tuple(low + step * i for i in range(n_grid))
    fitted_w = tuple(fit.total_variance(k) for k in grid_k)
    return SlicePlotSeries(raw_k=raw_k, raw_w=raw_w, grid_k=grid_k, fitted_w=fitted_w)


def _slice_stamp(
    fit: SliceFit, *, source_snapshot_ts: datetime, calc_ts: datetime, config_hash: str
) -> ProvenanceStamp:
    """Stamp a surface output, naming the IvPoints that fed the slice as sources."""
    refs = tuple(
        source_ref("iv_points", source_snapshot_ts, point.contract_key)
        for point in fit.raw_points
    )
    return stamp(
        calc_ts=calc_ts,
        code_version=SURFACE_VERSION,
        config_hash=config_hash,
        source_records=refs,
        source_timestamps=tuple(source_snapshot_ts for _ in refs),
    )


def surface_parameters(
    fit: SliceFit,
    *,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hash: str,
) -> SurfaceParameters:
    """Project a calibrated SVI slice into A's stamped ``SurfaceParameters``.

    Raises ``ValueError`` for a non-SVI slice — a nonparametric or insufficient slice
    has no SVI parameters to persist, and is never dressed up as a calibrated model.
    """
    if fit.method != METHOD_SVI or fit.svi is None:
        raise ValueError(f"cannot emit SurfaceParameters for a {fit.method} slice")
    return SurfaceParameters(
        snapshot_ts=snapshot_ts,
        underlying=fit.underlying,
        maturity_years=fit.maturity_years,
        model_version=SURFACE_VERSION,
        svi_a=fit.svi.a,
        svi_b=fit.svi.b,
        svi_rho=fit.svi.rho,
        svi_m=fit.svi.m,
        svi_sigma=fit.svi.sigma,
        expiry_date=fit.expiry_date,
        day_count=fit.day_count,
        diagnostics=SurfaceFitDiagnostics(
            rmse=fit.rmse, n_points=fit.n_points, arb_free=fit.arb_free
        ),
        source_snapshot_ts=source_snapshot_ts,
        provenance=_slice_stamp(
            fit, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts, config_hash=config_hash
        ),
    )


def surface_grid_cells(
    fit: SliceFit,
    moneyness_buckets: tuple[float, ...],
    *,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hash: str,
) -> tuple[SurfaceGrid, ...]:
    """Reconstruct a regularized total-variance grid for one slice (any method).

    One :class:`~contracts.SurfaceGrid` cell per moneyness bucket, the total variance
    read off the fitted curve (clamped at zero so the contract's non-negativity
    holds). Raises ``ValueError`` for an ``insufficient`` slice. Every cell shares the
    slice's provenance stamp.
    """
    if fit.method == METHOD_INSUFFICIENT:
        raise ValueError("cannot build a grid for an insufficient slice")
    provenance = _slice_stamp(
        fit, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts, config_hash=config_hash
    )
    cells: list[SurfaceGrid] = []
    for bucket in moneyness_buckets:
        cells.append(
            SurfaceGrid(
                snapshot_ts=snapshot_ts,
                underlying=fit.underlying,
                maturity_years=fit.maturity_years,
                moneyness_bucket=bucket,
                model_version=SURFACE_VERSION,
                total_variance=max(fit.total_variance(bucket), 0.0),
                source_snapshot_ts=source_snapshot_ts,
                provenance=provenance,
            )
        )
    return tuple(cells)
