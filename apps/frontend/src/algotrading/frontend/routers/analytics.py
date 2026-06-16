from __future__ import annotations

import math

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    ProjectedOptionAnalytics,
    SurfaceGrid,
    SurfaceParameters,
)
from algotrading.infra.surfaces import reconstruct_dense_surface
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep, TradeDateDep
from ..serializers import (
    dense_surface_to_dict,
    projected_option_analytics_to_dict,
    surface_parameters_to_dict,
)
from ..store_reads import read_for_underlying

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _maturity_key(maturity_years: float) -> str:
    return f"{maturity_years:.6f}"


def _group_by_maturity(
    cells: list[ProjectedOptionAnalytics],
    slices: list[SurfaceParameters],
) -> list[dict[str, object]]:
    slice_by_maturity = {_maturity_key(s.maturity_years): s for s in slices}
    grouped: dict[str, list[ProjectedOptionAnalytics]] = {}
    for cell in cells:
        if cell.surface_side != SURFACE_SIDE_COMBINED:
            continue
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        maturity_cells = sorted(grouped[key], key=lambda c: c.target_delta)
        points = [projected_option_analytics_to_dict(cell) for cell in maturity_cells]
        tenor_label = maturity_cells[0].tenor_label
        fitted = slice_by_maturity.get(key)
        entries.append(
            {
                "maturity_years": maturity_cells[0].maturity_years,
                "tenor_label": tenor_label,
                "label": f"{tenor_label} ({maturity_cells[0].maturity_years:.3f}y)",
                "smile": {
                    "axis_type": "delta",
                    "deltas": [cell.target_delta for cell in maturity_cells],
                    "implied_vols": [cell.implied_vol for cell in maturity_cells],
                    "log_moneyness": [cell.log_moneyness for cell in maturity_cells],
                },
                "surface_slice": (
                    surface_parameters_to_dict(fitted) if fitted is not None else None
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
                "points": [],
            }
        )
    return entries


@router.get("")
def get_analytics(
    ctx: CtxDep, trade_date: TradeDateDep, underlying: str | None = None
) -> JSONResponse:
    resolved_underlying = underlying or ctx.default_underlying
    cells: list[ProjectedOptionAnalytics] = read_for_underlying(
        ctx.store, "projected_option_analytics", resolved_underlying, trade_date=trade_date
    )
    slices: list[SurfaceParameters] = read_for_underlying(
        ctx.store, "surface_parameters", resolved_underlying, trade_date=trade_date
    )
    maturities = _group_by_maturity(cells, slices)
    source = "projected_option_analytics"
    if not maturities:
        grid: list[SurfaceGrid] = read_for_underlying(
            ctx.store, "surface_grid", resolved_underlying, trade_date=trade_date
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
