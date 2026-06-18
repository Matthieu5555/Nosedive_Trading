from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from algotrading.core.config import (
    ConfigError,
    ConfigFieldError,
    load_platform_config,
)
from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    SURFACE_SIDES,
    ForwardCurvePoint,
    MarketStateSnapshot,
    ProjectedOptionAnalytics,
    RiskFreeRatePoint,
    SurfaceGrid,
    SurfaceParameters,
)
from algotrading.infra.rates import (
    RateCurve,
    RateCurveError,
    RateIngestError,
    curve_from_points,
    implied_riskfree_spread,
)
from algotrading.infra.surfaces import reconstruct_dense_surface
from algotrading.infra.universe import IndexRegistryError, load_index_registry
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep, TradeDateDep
from ..grounding import coverage_from_snapshots, resolve_close_instant
from ..serializers import (
    OptionQuote,
    dense_surface_to_dict,
    forward_rate_diagnostics_to_dict,
    implied_riskfree_spread_to_dict,
    projected_option_analytics_to_dict,
    rate_curve_to_dict,
    surface_parameters_to_dict,
)
from ..store_reads import read_for_underlying

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_CANONICAL_FIELD_COUNT = 9
_EXPIRY_SLOT = 6
_STRIKE_SLOT = 7
_RIGHT_SLOT = 8


def _maturity_key(maturity_years: float) -> str:
    return f"{maturity_years:.6f}"


def _option_right_for_band(delta_band: str) -> str | None:
    if delta_band.endswith("p"):
        return "P"
    if delta_band.endswith("c"):
        return "C"
    return None


def _listed_options_by_expiry_right(
    snapshots: list[MarketStateSnapshot],
) -> dict[tuple[date, str], list[tuple[float, MarketStateSnapshot]]]:
    index: dict[tuple[date, str], list[tuple[float, MarketStateSnapshot]]] = {}
    for snapshot in snapshots:
        fields = snapshot.instrument_key.split("|")
        if len(fields) != _CANONICAL_FIELD_COUNT:
            continue
        expiry_text = fields[_EXPIRY_SLOT]
        strike_text = fields[_STRIKE_SLOT]
        right = fields[_RIGHT_SLOT]
        if not expiry_text or not strike_text or right not in ("C", "P"):
            continue
        try:
            expiry = date.fromisoformat(expiry_text)
            strike = float(strike_text)
        except ValueError:
            continue
        index.setdefault((expiry, right), []).append((strike, snapshot))
    return index


_STRIKE_MATCH_REL_TOL = 0.005


def _nearest_quote(
    listed: list[tuple[float, MarketStateSnapshot]], strike: float
) -> OptionQuote | None:
    if not listed or strike <= 0.0:
        return None
    listed_strike, snapshot = min(listed, key=lambda pair: abs(pair[0] - strike))
    if abs(listed_strike - strike) > _STRIKE_MATCH_REL_TOL * strike:
        return None
    return OptionQuote(bid=snapshot.bid, ask=snapshot.ask, volume=snapshot.volume)


def _quote_for_cell(
    cell: ProjectedOptionAnalytics,
    expiry: date | None,
    index: dict[tuple[date, str], list[tuple[float, MarketStateSnapshot]]],
) -> OptionQuote | None:
    if expiry is None:
        return None
    right = _option_right_for_band(cell.delta_band)
    if right is None:
        return None
    return _nearest_quote(index.get((expiry, right), []), cell.strike)


# The projected analytics live on a fixed reading-tenor grid (10d / 1m / 3m / 6m / 12m / 18m),
# while the fitted slices, forwards and listed-option snapshots live on the ACTUAL captured
# expiries (2026-06-26, 2026-07-17, ...). The two grids never share a `_maturity_key`, so an
# exact-key join silently left every cell with no slice, no forward and no quote, the symptom the
# PM saw as blank bid / ask / spread / volume columns. We instead pair each reading tenor with its
# NEAREST captured maturity, within a relative tolerance so a tenor with no captured neighbour stays
# honestly unpaired (e.g. a 3y read against a chain that stops at 18m) rather than being yoked to a
# far-off expiry.
_MATURITY_MATCH_REL_TOL = 0.25


def _nearest_by_maturity[T](
    maturity_years: float,
    candidates: list[tuple[float, T]],
) -> T | None:
    """The candidate whose maturity is closest to `maturity_years`, within the relative tolerance.

    `candidates` is a list of (candidate_maturity_years, candidate) pairs. Returns None when nothing
    is within tolerance (the reading tenor falls outside the captured chain), so the caller surfaces
    an honest gap instead of a mismatched join.
    """
    if not candidates or maturity_years <= 0.0:
        return None
    nearest_maturity, nearest = min(
        candidates, key=lambda pair: abs(pair[0] - maturity_years)
    )
    if abs(nearest_maturity - maturity_years) > _MATURITY_MATCH_REL_TOL * maturity_years:
        return None
    return nearest


def _smile_axis_cells(
    maturity_cells: list[ProjectedOptionAnalytics],
) -> list[ProjectedOptionAnalytics]:
    deduped: list[ProjectedOptionAnalytics] = []
    seen: set[float] = set()
    for cell in maturity_cells:
        if cell.target_delta in seen:
            continue
        seen.add(cell.target_delta)
        deduped.append(cell)
    return deduped


@dataclass(frozen=True, slots=True)
class _RateContext:
    """The ingested external curve + QC bound resolved for the analytics currency (ADR 0054)."""

    currency: str
    curve: RateCurve
    points: list[RiskFreeRatePoint]
    abs_bound: float
    disposition: str


def _load_rate_context(
    ctx: object, underlying: str, trade_date: date | None
) -> _RateContext | None:
    """Read the as-of `rates` partition for the underlying's currency and build the curve.

    Reads only the curve published as-of `trade_date` (no look-ahead — the store reads the partition
    for that day). Returns None when the currency, config, or rows are unavailable; the rest of the
    payload stays unaffected (additive surface).
    """
    try:
        registry = load_index_registry(ctx.configs_dir)  # type: ignore[attr-defined]
        currency = registry.get(underlying).currency
        platform = load_platform_config(ctx.configs_dir)  # type: ignore[attr-defined]
        currency_cfg = platform.rates.for_currency(currency)
    except (ConfigError, ConfigFieldError, IndexRegistryError, KeyError):
        return None
    rows: list[RiskFreeRatePoint] = ctx.store.read(  # type: ignore[attr-defined]
        "rates", trade_date=trade_date, underlying=currency
    )
    rows = [row for row in rows if row.currency == currency]
    if not rows:
        return None
    try:
        curve = curve_from_points(currency, rows)
    except (RateIngestError, RateCurveError):
        return None
    return _RateContext(
        currency=currency,
        curve=curve,
        points=rows,
        abs_bound=currency_cfg.spread_qc_abs_bound,
        disposition=currency_cfg.spread_qc_disposition,
    )


def _spread_for_maturity(
    forward: ForwardCurvePoint | None,
    rate_context: _RateContext | None,
) -> dict[str, object] | None:
    """Per-maturity external r(T) + implied−riskfree spread, from PERSISTED inputs only.

    Evaluates the ingested curve at the option's maturity (a read-time projection of the persisted
    pillars) and pairs it with the forward's persisted `implied_rate`. Returns None when either the
    curve or the implied rate is unavailable — never a recompute of any analytics value.
    """
    if rate_context is None or forward is None or forward.implied_rate is None:
        return None
    try:
        risk_free_rate = rate_context.curve.rate_at(forward.maturity_years)
    except RateCurveError:
        return None
    spread = implied_riskfree_spread(
        currency=rate_context.currency,
        maturity_years=forward.maturity_years,
        implied_rate=forward.implied_rate,
        risk_free_rate=risk_free_rate,
        abs_bound=rate_context.abs_bound,
        disposition=rate_context.disposition,
    )
    return implied_riskfree_spread_to_dict(spread)


def _group_by_maturity(
    cells: list[ProjectedOptionAnalytics],
    slices: list[SurfaceParameters],
    snapshots: list[MarketStateSnapshot],
    forwards: list[ForwardCurvePoint],
    rate_context: _RateContext | None = None,
    surface_side: str = SURFACE_SIDE_COMBINED,
) -> list[dict[str, object]]:
    """Maturity entries for ONE surface side (combined / call / put).

    The captured store carries the same maturities for each side, but call and put cells differ in
    IV (the two wings have genuinely different skew, per the captured SX5E close), so a per-side
    grouping is real data, not a re-slice of combined. The fitted SVI `surface_slice` is per-maturity
    (no side discriminator), so every side reads the same slice diagnostics — honest, since only one
    fit exists per maturity.
    """
    # The reading-tenor cell grid does not line up with the captured-expiry slices / forwards, so we
    # pair each by NEAREST maturity (within tolerance), not by an exact key that never matches. An
    # exact key still wins when present (the seeded-test case), since it is also the nearest.
    slice_candidates = [(s.maturity_years, s) for s in slices]
    forward_candidates = [(f.maturity_years, f) for f in forwards]
    quote_index = _listed_options_by_expiry_right(snapshots)
    grouped: dict[str, list[ProjectedOptionAnalytics]] = {}
    for cell in cells:
        if cell.surface_side != surface_side:
            continue
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        maturity_cells = sorted(grouped[key], key=lambda c: c.target_delta)
        tenor_label = maturity_cells[0].tenor_label
        maturity_years = maturity_cells[0].maturity_years
        fitted = _nearest_by_maturity(maturity_years, slice_candidates)
        expiry = fitted.expiry_date if fitted is not None else None
        points = [
            projected_option_analytics_to_dict(
                cell, _quote_for_cell(cell, expiry, quote_index)
            )
            for cell in maturity_cells
        ]
        forward = _nearest_by_maturity(maturity_years, forward_candidates)
        smile_cells = _smile_axis_cells(maturity_cells)
        entries.append(
            {
                "maturity_years": maturity_cells[0].maturity_years,
                "tenor_label": tenor_label,
                "label": f"{tenor_label} ({maturity_cells[0].maturity_years:.3f}y)",
                "smile": {
                    "axis_type": "delta",
                    "deltas": [cell.target_delta for cell in smile_cells],
                    "implied_vols": [cell.implied_vol for cell in smile_cells],
                    "log_moneyness": [cell.log_moneyness for cell in smile_cells],
                },
                "surface_slice": (
                    surface_parameters_to_dict(fitted) if fitted is not None else None
                ),
                "rate_diagnostics": (
                    forward_rate_diagnostics_to_dict(forward) if forward is not None else None
                ),
                "rate_curve": _spread_for_maturity(forward, rate_context),
                "points": points,
            }
        )
    return entries


def _maturities_from_surface_grid(
    grid: list[SurfaceGrid],
    slices: list[SurfaceParameters],
) -> list[dict[str, object]]:
    slice_by_maturity = {_maturity_key(s.maturity_years): s for s in slices}
    grouped: dict[str, list[SurfaceGrid]] = {}
    for cell in grid:
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        cells = sorted(grouped[key], key=lambda c: c.moneyness_bucket)
        maturity_years = cells[0].maturity_years
        buckets = [cell.moneyness_bucket for cell in cells]
        implied_vols = [
            math.sqrt(cell.total_variance / cell.maturity_years)
            if cell.total_variance > 0.0 and cell.maturity_years > 0.0
            else 0.0
            for cell in cells
        ]
        fitted = slice_by_maturity.get(key)
        label = f"{maturity_years:.3f}y"
        entries.append(
            {
                "maturity_years": maturity_years,
                "tenor_label": label,
                "label": label,
                "smile": {
                    "axis_type": "moneyness",
                    "moneyness_buckets": buckets,
                    "implied_vols": implied_vols,
                    "log_moneyness": buckets,
                },
                "surface_slice": (
                    surface_parameters_to_dict(fitted) if fitted is not None else None
                ),
                "rate_diagnostics": None,
                "points": [],
            }
        )
    return entries


_PER_SIDE_DENSE_N_K = 41


def _interp_iv(k: float, ks: list[float], ivs: list[float]) -> float | None:
    """Linear IV at `k` along one slice's (sorted) smile; None OUTSIDE the captured wing.

    Strictly an interpolation of captured cells (never an extrapolation): a `k` past the deepest
    quoted wing is a null hole, so the surface shows where the side stopped quoting rather than
    inventing a level. A `k` between two quoted points is linearly blended (the same regularize-only
    read the smooth fit applies, with no model fabrication).
    """
    if not ks or k < ks[0] or k > ks[-1]:
        return None
    for left, right in zip(range(len(ks) - 1), range(1, len(ks)), strict=False):
        k_lo, k_hi = ks[left], ks[right]
        if k_lo <= k <= k_hi:
            if k_hi == k_lo:
                return ivs[left]
            weight = (k - k_lo) / (k_hi - k_lo)
            return ivs[left] + weight * (ivs[right] - ivs[left])
    return ivs[-1]


def _dense_from_maturity_entries(
    entries: list[dict[str, object]],
    model_version: str,
) -> dict[str, object] | None:
    """A clean dense (maturity x log-moneyness) IV grid built from the per-side smile cells.

    The fitted SVI dense surface (`reconstruct_dense_surface`) has no side, so for the call/put views
    we build the grid from the captured per-side cells. To read as a smooth nappe (not a ragged union
    of every slice's distinct strikes), every maturity is resampled onto ONE regular log-moneyness
    axis spanning the captured wings; each cell is a linear interpolation of that slice's quotes, and a
    point past the slice's deepest quoted wing is a null hole (never extrapolated, blueprint "show the
    gaps"). Needs >= 2 maturities to read as a surface; otherwise None and the 2D smile is the honest
    read.
    """
    usable = [
        entry
        for entry in entries
        if isinstance(entry.get("smile"), dict)
        and entry["smile"].get("log_moneyness")  # type: ignore[union-attr]
    ]
    if len(usable) < 2:
        return None
    usable = sorted(usable, key=lambda e: float(e["maturity_years"]))  # type: ignore[arg-type]

    # One regular k-axis across the captured wings, so the grid is rectangular and smooth.
    all_k = [
        float(k)
        for entry in usable
        for k in entry["smile"]["log_moneyness"]  # type: ignore[index]
    ]
    k_min, k_max = min(all_k), max(all_k)
    if k_max <= k_min:
        return None
    k_axis = [
        k_min + (k_max - k_min) * i / (_PER_SIDE_DENSE_N_K - 1)
        for i in range(_PER_SIDE_DENSE_N_K)
    ]

    maturities = [float(entry["maturity_years"]) for entry in usable]  # type: ignore[arg-type]
    grid: list[list[float | None]] = []
    degenerate: list[float] = []
    for entry in usable:
        smile = entry["smile"]  # type: ignore[index]
        pairs = sorted(
            zip(smile["log_moneyness"], smile["implied_vols"], strict=False),
            key=lambda p: p[0],
        )
        ks = [float(k) for k, _ in pairs]
        ivs = [float(iv) for _, iv in pairs]
        grid.append([_interp_iv(k, ks, ivs) for k in k_axis])
        fitted = entry.get("surface_slice")
        if isinstance(fitted, dict) and fitted.get("degenerate"):
            degenerate.append(float(entry["maturity_years"]))  # type: ignore[arg-type]
    return {
        "log_moneyness": k_axis,
        "maturity_years": maturities,
        "implied_vol": grid,
        "model_version": model_version,
        "degenerate_maturity_years": degenerate,
    }


@router.get("")
def get_analytics(
    ctx: CtxDep,
    trade_date: TradeDateDep,
    underlying: str | None = None,
) -> JSONResponse:
    resolved_underlying = underlying or ctx.default_underlying
    cells: list[ProjectedOptionAnalytics] = read_for_underlying(
        ctx.store,
        "projected_option_analytics",
        resolved_underlying,
        trade_date=trade_date,
    )
    slices: list[SurfaceParameters] = read_for_underlying(
        ctx.store,
        "surface_parameters",
        resolved_underlying,
        trade_date=trade_date,
    )
    snapshots: list[MarketStateSnapshot] = read_for_underlying(
        ctx.store,
        "market_state_snapshots",
        resolved_underlying,
        trade_date=trade_date,
    )
    forwards: list[ForwardCurvePoint] = read_for_underlying(
        ctx.store,
        "forward_curve",
        resolved_underlying,
        trade_date=trade_date,
    )
    rate_context = _load_rate_context(ctx, resolved_underlying, trade_date)
    maturities = _group_by_maturity(
        cells, slices, snapshots, forwards, rate_context, SURFACE_SIDE_COMBINED
    )
    source = "projected_option_analytics"
    if not maturities:
        grid: list[SurfaceGrid] = read_for_underlying(
            ctx.store,
            "surface_grid",
            resolved_underlying,
            trade_date=trade_date,
        )
        maturities = _maturities_from_surface_grid(grid, slices)
        if maturities:
            source = "surface_grid"
    dense = reconstruct_dense_surface(slices)
    combined_dense = dense_surface_to_dict(dense) if dense is not None else None

    # Per-side views (call / put / combined). The captured store carries genuinely different IV for
    # the call wing and the put wing, so each side is its own data, not a re-slice of combined. Each
    # side carries its own maturities (smile + per-band Greek points) and its own dense 3D grid built
    # from those cells. `combined` reuses the maturities/dense already computed above (the default,
    # backward-compatible `maturities`/`surface`). A side the close did not capture serializes as an
    # empty maturity list + null dense, so the front degrades to an honest "per-side fit not available
    # for this close, showing combined", never a fabricated surface.
    sides: dict[str, list[dict[str, object]]] = {}
    surfaces_by_side: dict[str, dict[str, object] | None] = {}
    model_version = dense.model_version if dense is not None else "svi"
    for side in SURFACE_SIDES:
        if side == SURFACE_SIDE_COMBINED:
            side_entries = maturities
            # The combined 3D prefers the smooth fitted-SVI reconstruction (the existing `surface`),
            # and falls back to the cell grid when the fit produced no dense surface (e.g. a single
            # fitted slice), so the per-side toggle always has a combined grid when call/put do.
            side_dense = combined_dense or _dense_from_maturity_entries(
                side_entries, model_version
            )
        else:
            side_entries = _group_by_maturity(
                cells, slices, snapshots, forwards, rate_context, side
            )
            side_dense = _dense_from_maturity_entries(side_entries, model_version)
        sides[side] = side_entries
        surfaces_by_side[side] = side_dense

    coverage = coverage_from_snapshots(snapshots)
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": trade_date.isoformat() if trade_date else None,
            "close_instant": resolve_close_instant(ctx, resolved_underlying, trade_date),
            "n_maturities": len(maturities),
            "source": source,
            "maturities": maturities,
            "surface": combined_dense,
            "sides": sides,
            "surfaces_by_side": surfaces_by_side,
            "sides_available": sorted(
                side for side, entries in sides.items() if entries
            ),
            "coverage": coverage.to_dict() if coverage.option_rows > 0 else None,
            "rate_curve": (
                rate_curve_to_dict(
                    rate_context.currency, rate_context.curve, rate_context.points
                )
                if rate_context is not None
                else None
            ),
        }
    )
