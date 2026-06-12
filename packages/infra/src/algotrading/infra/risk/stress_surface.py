"""Stress surface (roadmap Phase 2 / WS 2B): the full cartesian (spot × vol) PnL surface.

Where :mod:`algotrading.infra.risk.scenarios` builds the *families* grid (a spot family, a
vol family, one combined crash, a time roll), this module builds the **full cartesian
product** of a symmetric spot-shock axis × a symmetric vol-shock axis — the grid the 2B
stress page renders as a Plotly 3-D surface. Every cell is a **full reprice** of the book
under that explicit shocked state (ADR 0006): this module owns the grid construction and the
z-grid arrangement only, and reuses the trusted reprice primitives
(:func:`scenarios.scenario_line_pnls`, :func:`scenarios.scenario_totals`) rather than forking
a second repricer.

Shock conventions are the engine's, unchanged (see :mod:`scenarios`): ``spot_shock`` is
relative (``new_spot = spot*(1+s)``), ``vol_shock`` is additive (``new_vol = vol+v``); the
surface holds ``time_shock = 0`` (it stresses the snapshot, no roll-down). Each axis is
*symmetric* (``[-abs, +abs]``) and sampled on an *odd* number of points so the centre cell
(0 spot, 0 vol) — ≈ 0 PnL by construction — is always present.

The ranges and step counts are **config** (:class:`algotrading.core.config.StressSurfaceConfig`
in ``scenarios.yaml``), hashed into ``config_hashes["scenarios"]`` and folded into
:func:`effective_surface_version` so the production ±50%/±50% grid is a YAML edit, never a
``.py`` literal (ADR 0028).

Version note: :func:`effective_surface_version` stays a *separate* version from
:func:`scenarios.effective_scenario_version` — it folds that version plus the
surface-construction hash, a strict superset. The shared *encoding* (ordered de-dup,
canonical-JSON short hash) now lives in :mod:`.grid_versioning` (the 2C-deferral
follow-up, landed as M44); the persisted version strings are byte-identical.
"""

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

# The surface-construction policy, versioned independently of the economic config. Bump on
# any change to how the cartesian grid is built (the axis sampling, the id format); it is
# hashed into the persisted surface version so two different surfaces can never share one.
SURFACE_CONSTRUCTION_VERSION = "surface-1.0.0"

# The scenario family tag carried by every surface cell, distinguishing the cartesian
# surface scenarios from the families grid's spot/vol/combined/time scenarios.
SURFACE_FAMILY = "surface"

# Snap-to-zero tolerance for the floating axis midpoint, so the centre cell is literally
# 0.0 shock (and dedups/looks-up cleanly) rather than a ~1e-17 residue.
_CENTRE_EPS = 1e-12
# Axis values are rounded to this many decimals before de-dup so a floating residue never
# splits one logical shock into two near-equal cells.
_AXIS_DECIMALS = 10


def _symmetric_axis(abs_mag: float, steps: int) -> tuple[float, ...]:
    """A symmetric shock axis of ``steps`` points over ``[-abs_mag, +abs_mag]``.

    ``steps`` is odd (enforced by :class:`StressSurfaceConfig`), so the centre point is
    exactly 0. ``steps == 1`` or a zero-width range (``abs_mag == 0``) collapses to the
    single centre column ``(0.0,)``.
    """
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
    """Stable id for one surface cell, fixed format so the grid is a pure function of config."""
    return f"surf_s{spot_shock:+.4f}_v{vol_shock:+.4f}"


def surface_axes(config: ScenarioConfig) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The (spot-shock axis, vol-shock axis) the surface is built over, from config."""
    surface = config.stress_surface
    return (
        _symmetric_axis(surface.spot_shock_abs, surface.spot_steps),
        _symmetric_axis(surface.vol_shock_abs, surface.vol_steps),
    )


def stress_surface_grid(config: ScenarioConfig) -> tuple[Scenario, ...]:
    """The deterministic full cartesian (spot × vol) surface grid from config.

    Emits one :class:`Scenario` per ``(spot_shock, vol_shock)`` pair (``time_shock = 0``),
    in spot-major then vol-minor order, with stable ids — so the grid is a pure function of
    :class:`StressSurfaceConfig`. The cell count is exactly ``len(spot_axis) × len(vol_axis)``.
    """
    spot_axis, vol_axis = surface_axes(config)
    scenarios = tuple(
        Scenario(_surface_scenario_id(s, v), SURFACE_FAMILY, s, v, 0.0)
        for s in spot_axis
        for v in vol_axis
    )
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(set(ids)) != len(ids):
        # Distinct axis points that format to the same 4-dp id — a precision collision. Guard
        # it loudly rather than letting a surface cell silently collapse downstream.
        raise ScenarioGridError(f"stress surface grid has colliding ids: {sorted(ids)}")
    return scenarios


def _surface_construction_hash(surface: StressSurfaceConfig) -> str:
    """A short, stable (cross-process) hash of the surface-construction inputs.

    Folded into :func:`effective_surface_version`, so changing the stress range/steps (or the
    construction policy) moves the persisted version automatically. The encoding (canonical-
    JSON SHA-256, 12 hex chars) is the shared :func:`~.grid_versioning.short_construction_hash`
    — byte-identical to the inline copy it replaced.
    """
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
    """The version to persist on every surface cell.

    A strict superset of :func:`scenarios.effective_scenario_version`: it folds that version
    (config section version + families grid-construction hash) with the surface-construction
    hash, so a surface regenerates exactly from positions + snapshot + this version, and
    editing the stress range/steps moves it even when ``config.version`` does not.
    """
    scenario_version = effective_scenario_version(config)
    return f"{scenario_version}+{_surface_construction_hash(config.stress_surface)}"


@dataclass(frozen=True, slots=True)
class StressSurface:
    """A book's full-reprice PnL over the cartesian (spot-shock × vol-shock) grid.

    ``pnl_grid[i][j]`` is the portfolio's full-reprice PnL when spot is shocked by
    ``spot_axis[i]`` (relative) and vol by ``vol_axis[j]`` (additive) — the exact z-grid a
    Plotly ``surface`` renders, aligned to the two axes. The centre cell (spot 0, vol 0) is
    ≈ 0 by construction. ``scenario_version`` (:func:`effective_surface_version`) makes the
    surface regenerable from positions + snapshot.
    """

    scenario_version: str
    spot_axis: tuple[float, ...]
    vol_axis: tuple[float, ...]
    pnl_grid: tuple[tuple[float, ...], ...]


def _european_grid_pnls(
    lines: list[PositionRisk],
    spot_axis: tuple[float, ...],
    vol_axis: tuple[float, ...],
) -> list[list[float]]:
    """The full-reprice PnL grid (spot × vol) for the European lines, summed over them.

    Closed-form Black-76 over the whole cartesian grid in one ``(legs, spot, vol)`` array
    (:func:`pricing.price_european_array`), held bit-faithful to the scalar engine. The
    surface holds ``time_shock = 0`` (the grid asserts it), so maturity and the discount
    factor are unchanged under the shock — only the forward (``forward*(1+s)``) and the vol
    (``max(vol+v, 0)``) move, the same state :func:`scenarios.shock_valuation` builds. Returns
    an ``len(spot_axis) × len(vol_axis)`` grid; flat zero when there are no European lines.
    """
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
    # Broadcast to (legs, spot, vol): the per-leg state on axis 0, the spot shock on axis 1,
    # the vol shock on axis 2. carry and maturity are held fixed, so forward*(1+s) is exactly
    # the shocked forward (spot*(1+s) carried out to the unchanged maturity).
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
    """Full-reprice the book over the cartesian surface grid and arrange it as a z-grid.

    Each cell is the portfolio's full-reprice PnL under that ``(spot_shock, vol_shock)``
    state, summed over the netted lines. European lines are repriced in closed form over the
    whole grid at once (:func:`_european_grid_pnls`); any non-European line (the American
    lattice) keeps the scalar :func:`scenarios.scenario_line_pnls` path, and the two add
    cell-by-cell — so the surface is the same full reprice, just without a per-cell
    ``PricingState`` construction for the European legs that dominate the basket page. An
    empty book reprices to a flat-zero surface over the configured axes (the honest full
    reprice of nothing); the axes are a pure function of config, independent of the book.
    ``steps`` is forwarded to the American lattice and ignored for European contracts.
    """
    spot_axis, vol_axis = surface_axes(config)
    line_list = net_lots(lines)
    european = [ln for ln in line_list if ln.valuation.exercise_style == "european"]
    american = [ln for ln in line_list if ln.valuation.exercise_style != "european"]

    grid = _european_grid_pnls(european, spot_axis, vol_axis)
    if american:
        # The lattice has no closed form to vectorize; reprice it on the scalar path and
        # add it onto the European grid cell-by-cell (PnL is additive across lines).
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
