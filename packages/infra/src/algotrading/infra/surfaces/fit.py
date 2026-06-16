from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.core.config import SurfaceConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.contracts import (
    IvPoint,
    SurfaceFitDiagnostics,
    SurfaceGrid,
    SurfaceParameters,
)

from .arbitrage import butterfly_violations
from .svi import SURFACE_VERSION, SviFit, SviParams, fit_svi

METHOD_SVI = "svi"
METHOD_NONPARAMETRIC = "nonparametric"
METHOD_INSUFFICIENT = "insufficient"

_ARB_GRID_PAD = 0.1
_ARB_GRID_POINTS = 21


@dataclass(frozen=True, slots=True)
class SliceFit:

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
    converged: bool | None = None

    def total_variance(self, k: float) -> float:
        if self.method == METHOD_SVI and self.svi is not None:
            return self.svi.total_variance(k)
        if self.method == METHOD_NONPARAMETRIC:
            return _interpolate_sorted(self.nonparametric_ks, self.nonparametric_ws, k)
        raise ValueError(f"slice for {self.underlying} has no curve ({self.method})")


def _interpolate_sorted(ks: tuple[float, ...], ws: tuple[float, ...], k: float) -> float:
    if k != k:
        raise ValueError("interpolation query k must not be NaN")
    if k <= ks[0]:
        return ws[0]
    if k >= ks[-1]:
        return ws[-1]
    index = bisect_left(ks, k)
    span = ks[index] - ks[index - 1]
    weight = (k - ks[index - 1]) / span
    return ws[index - 1] + weight * (ws[index] - ws[index - 1])


def _distinct_sorted_points(
    points: tuple[IvPoint, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    by_k: dict[float, float] = {}
    for point in points:
        if point.total_variance >= 0.0 and point.log_moneyness not in by_k:
            by_k[point.log_moneyness] = point.total_variance
    ks = tuple(sorted(by_k))
    return ks, tuple(by_k[k] for k in ks)


def _arb_grid(ks: tuple[float, ...]) -> tuple[float, ...]:
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
    config: SurfaceConfig,
) -> SliceFit:
    ks, ws = _distinct_sorted_points(points)

    if len(ks) >= config.min_points_per_slice:
        svi_fit: SviFit = fit_svi(ks, ws, config=config)
        breaches = butterfly_violations(svi_fit.params, _arb_grid(ks))
        return SliceFit(
            underlying=underlying, maturity_years=maturity_years, expiry_date=expiry_date,
            day_count=day_count, method=config.model, svi=svi_fit.params, rmse=svi_fit.rmse,
            n_points=svi_fit.n_points, arb_free=not breaches, bound_hits=svi_fit.bound_hits,
            butterfly_violations=breaches, nonparametric_ks=ks, nonparametric_ws=ws,
            raw_points=points, converged=svi_fit.converged,
        )

    if len(ks) >= 1:
        return SliceFit(
            underlying=underlying, maturity_years=maturity_years, expiry_date=expiry_date,
            day_count=day_count, method=config.fallback_model, svi=None, rmse=0.0,
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

    raw_k: tuple[float, ...]
    raw_w: tuple[float, ...]
    grid_k: tuple[float, ...]
    fitted_w: tuple[float, ...]


def slice_plot_series(fit: SliceFit, *, n_grid: int = 50) -> SlicePlotSeries:
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
    fit: SliceFit,
    *,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> ProvenanceStamp:
    refs = tuple(
        source_ref("iv_points", source_snapshot_ts, point.contract_key)
        for point in fit.raw_points
    )
    return stamp(
        calc_ts=calc_ts,
        code_version=SURFACE_VERSION,
        config_hashes=config_hashes,
        source_records=refs,
        source_timestamps=tuple(source_snapshot_ts for _ in refs),
    )


def surface_parameters(
    fit: SliceFit,
    *,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> SurfaceParameters:
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
            rmse=fit.rmse, n_points=fit.n_points, arb_free=fit.arb_free,
            bound_hits=fit.bound_hits, converged=fit.converged,
        ),
        source_snapshot_ts=source_snapshot_ts,
        provenance=_slice_stamp(
            fit, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts, config_hashes=config_hashes
        ),
    )


def degeneracy_reasons(diagnostics: SurfaceFitDiagnostics) -> tuple[str, ...]:
    reasons: list[str] = []
    for name in diagnostics.bound_hits or ():
        reasons.append(f"param_at_bound:{name}")
    if diagnostics.converged is False:
        reasons.append("not_converged")
    if not diagnostics.arb_free:
        reasons.append("butterfly_arbitrage")
    return tuple(reasons)


def surface_grid_cells(
    fit: SliceFit,
    moneyness_buckets: tuple[float, ...],
    *,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> tuple[SurfaceGrid, ...]:
    if fit.method == METHOD_INSUFFICIENT:
        raise ValueError("cannot build a grid for an insufficient slice")
    provenance = _slice_stamp(
        fit, source_snapshot_ts=source_snapshot_ts, calc_ts=calc_ts, config_hashes=config_hashes
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


@dataclass(frozen=True, slots=True)
class SurfaceProjection:

    parameters: SurfaceParameters | None
    grid_cells: tuple[SurfaceGrid, ...]


def project_surface_fit(
    fit: SliceFit,
    moneyness_buckets: tuple[float, ...],
    *,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> SurfaceProjection:
    if fit.method == METHOD_INSUFFICIENT:
        return SurfaceProjection(parameters=None, grid_cells=())
    parameters = (
        surface_parameters(
            fit,
            snapshot_ts=snapshot_ts,
            source_snapshot_ts=source_snapshot_ts,
            calc_ts=calc_ts,
            config_hashes=config_hashes,
        )
        if fit.method == METHOD_SVI
        else None
    )
    cells = surface_grid_cells(
        fit,
        moneyness_buckets,
        snapshot_ts=snapshot_ts,
        source_snapshot_ts=source_snapshot_ts,
        calc_ts=calc_ts,
        config_hashes=config_hashes,
    )
    return SurfaceProjection(parameters=parameters, grid_cells=cells)
