from __future__ import annotations

import math
from datetime import date

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    ForwardCurvePoint,
    MarketStateSnapshot,
    ProjectedOptionAnalytics,
    SurfaceGrid,
    SurfaceParameters,
)
from algotrading.infra.surfaces import reconstruct_dense_surface
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep, TradeDateDep
from ..serializers import (
    OptionQuote,
    dense_surface_to_dict,
    forward_rate_diagnostics_to_dict,
    projected_option_analytics_to_dict,
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


def _group_by_maturity(
    cells: list[ProjectedOptionAnalytics],
    slices: list[SurfaceParameters],
    snapshots: list[MarketStateSnapshot],
    forwards: list[ForwardCurvePoint],
) -> list[dict[str, object]]:
    slice_by_maturity = {_maturity_key(s.maturity_years): s for s in slices}
    quote_index = _listed_options_by_expiry_right(snapshots)
    forward_by_maturity = {_maturity_key(f.maturity_years): f for f in forwards}
    grouped: dict[str, list[ProjectedOptionAnalytics]] = {}
    for cell in cells:
        if cell.surface_side != SURFACE_SIDE_COMBINED:
            continue
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        maturity_cells = sorted(grouped[key], key=lambda c: c.target_delta)
        tenor_label = maturity_cells[0].tenor_label
        fitted = slice_by_maturity.get(key)
        expiry = fitted.expiry_date if fitted is not None else None
        points = [
            projected_option_analytics_to_dict(
                cell, _quote_for_cell(cell, expiry, quote_index)
            )
            for cell in maturity_cells
        ]
        forward = forward_by_maturity.get(key)
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
    maturities = _group_by_maturity(cells, slices, snapshots, forwards)
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
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": trade_date.isoformat() if trade_date else None,
            "n_maturities": len(maturities),
            "source": source,
            "maturities": maturities,
            "surface": dense_surface_to_dict(dense) if dense is not None else None,
        }
    )
