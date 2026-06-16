from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from algotrading.core.config import ScenarioConfig, StressSurfaceConfig
from algotrading.infra.pricing import price_european_array

from .greeks import PositionRisk, net_lots
from .grid_versioning import dedup_preserving_order, short_construction_hash
from .scenarios import (
    Scenario,
    ScenarioGridError,
    effective_scenario_version,
    scenario_line_pnls,
    scenario_totals,
)

SURFACE_CONSTRUCTION_VERSION = "surface-1.0.0"

SURFACE_FAMILY = "surface"

_CENTRE_EPS = 1e-12
_AXIS_DECIMALS = 10


def _symmetric_axis(abs_mag: float, steps: int) -> tuple[float, ...]:
    if steps <= 1 or abs_mag == 0.0:
        return (0.0,)
    step = (2.0 * abs_mag) / (steps - 1)
    points: list[float] = []
    for i in range(steps):
        value = -abs_mag + i * step
        if abs(value) < _CENTRE_EPS:
            value = 0.0
        points.append(round(value, _AXIS_DECIMALS))
    return dedup_preserving_order(tuple(points))


def _surface_scenario_id(spot_shock: float, vol_shock: float) -> str:
    return f"surf_s{spot_shock:+.4f}_v{vol_shock:+.4f}"


def surface_axes(config: ScenarioConfig) -> tuple[tuple[float, ...], tuple[float, ...]]:
    surface = config.stress_surface
    return (
        _symmetric_axis(surface.spot_shock_abs, surface.spot_steps),
        _symmetric_axis(surface.vol_shock_abs, surface.vol_steps),
    )


def stress_surface_grid(config: ScenarioConfig) -> tuple[Scenario, ...]:
    spot_axis, vol_axis = surface_axes(config)
    scenarios = tuple(
        Scenario(_surface_scenario_id(s, v), SURFACE_FAMILY, s, v, 0.0)
        for s in spot_axis
        for v in vol_axis
    )
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(set(ids)) != len(ids):
        raise ScenarioGridError(f"stress surface grid has colliding ids: {sorted(ids)}")
    return scenarios


def _surface_construction_hash(surface: StressSurfaceConfig) -> str:
    payload = {
        "version": SURFACE_CONSTRUCTION_VERSION,
        "stress_version": surface.version,
        "spot_shock_abs": surface.spot_shock_abs,
        "vol_shock_abs": surface.vol_shock_abs,
        "spot_steps": surface.spot_steps,
        "vol_steps": surface.vol_steps,
    }
    return short_construction_hash(payload)


def effective_surface_version(config: ScenarioConfig) -> str:
    scenario_version = effective_scenario_version(config)
    return f"{scenario_version}+{_surface_construction_hash(config.stress_surface)}"


@dataclass(frozen=True, slots=True)
class StressSurface:

    scenario_version: str
    spot_axis: tuple[float, ...]
    vol_axis: tuple[float, ...]
    pnl_grid: tuple[tuple[float, ...], ...]


def _european_grid_pnls(
    lines: list[PositionRisk],
    spot_axis: tuple[float, ...],
    vol_axis: tuple[float, ...],
) -> list[list[float]]:
    n_spot, n_vol = len(spot_axis), len(vol_axis)
    if not lines:
        return [[0.0] * n_vol for _ in range(n_spot)]

    forward = np.array([ln.valuation.forward for ln in lines], dtype=np.float64)
    strike = np.array([ln.valuation.strike for ln in lines], dtype=np.float64)
    maturity = np.array([ln.valuation.maturity_years for ln in lines], dtype=np.float64)
    vol = np.array([ln.valuation.volatility for ln in lines], dtype=np.float64)
    discount = np.array([ln.valuation.discount_factor for ln in lines], dtype=np.float64)
    is_call = np.array([ln.valuation.option_right == "C" for ln in lines], dtype=bool)
    base_price = np.array([ln.greeks.price for ln in lines], dtype=np.float64)
    scale = np.array([ln.scale for ln in lines], dtype=np.float64)

    spot_shocks = np.asarray(spot_axis, dtype=np.float64)
    vol_shocks = np.asarray(vol_axis, dtype=np.float64)
    shocked_forward = forward[:, None, None] * (1.0 + spot_shocks[None, :, None])
    shocked_vol = np.maximum(vol[:, None, None] + vol_shocks[None, None, :], 0.0)
    priced = price_european_array(
        forward=shocked_forward,
        strike=strike[:, None, None],
        maturity_years=maturity[:, None, None],
        volatility=shocked_vol,
        discount_factor=discount[:, None, None],
        is_call=is_call[:, None, None],
    )
    pnl = (priced - base_price[:, None, None]) * scale[:, None, None]
    grid: list[list[float]] = pnl.sum(axis=0).tolist()
    return grid


def stress_surface(
    lines: Iterable[PositionRisk], config: ScenarioConfig, *, steps: int | None = None
) -> StressSurface:
    spot_axis, vol_axis = surface_axes(config)
    line_list = net_lots(lines)
    european = [ln for ln in line_list if ln.valuation.exercise_style == "european"]
    american = [ln for ln in line_list if ln.valuation.exercise_style != "european"]

    grid = _european_grid_pnls(european, spot_axis, vol_axis)
    if american:
        cells = scenario_line_pnls(american, stress_surface_grid(config), steps=steps)
        totals = scenario_totals(cells)
        grid = [
            [
                grid[i][j] + totals.get(_surface_scenario_id(s, v), 0.0)
                for j, v in enumerate(vol_axis)
            ]
            for i, s in enumerate(spot_axis)
        ]

    pnl_grid = tuple(tuple(row) for row in grid)
    return StressSurface(
        scenario_version=effective_surface_version(config),
        spot_axis=spot_axis,
        vol_axis=vol_axis,
        pnl_grid=pnl_grid,
    )
