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

Boundary note (WS 2C is concurrently refactoring ``scenarios.py``): this module deliberately
keeps a *local* :func:`effective_surface_version` rather than editing the shared
:func:`scenarios.effective_scenario_version`. It folds the existing scenario version plus the
surface-construction hash, so it is a strict superset; unifying the two is a follow-up once
the 2C claim on ``scenarios.py`` clears.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from algotrading.core.config import ScenarioConfig, StressSurfaceConfig

from .greeks import PositionRisk
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


def _dedup_preserving_order(values: tuple[float, ...]) -> tuple[float, ...]:
    """Drop duplicate axis points, keeping first-seen order (a degenerate range collapses
    to the single centre column)."""
    seen: set[float] = set()
    unique: list[float] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return tuple(unique)


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
    return _dedup_preserving_order(tuple(points))


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
    construction policy) moves the persisted version automatically. SHA-256 over canonical
    JSON — never Python's salted ``hash()`` — so it is identical in every run and machine.
    """
    payload = {
        "version": SURFACE_CONSTRUCTION_VERSION,
        "stress_version": surface.version,
        "spot_shock_abs": surface.spot_shock_abs,
        "vol_shock_abs": surface.vol_shock_abs,
        "spot_steps": surface.spot_steps,
        "vol_steps": surface.vol_steps,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


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


def stress_surface(
    lines: Iterable[PositionRisk], config: ScenarioConfig, *, steps: int | None = None
) -> StressSurface:
    """Full-reprice the book over the cartesian surface grid and arrange it as a z-grid.

    Each cell is the portfolio's full-reprice PnL under that ``(spot_shock, vol_shock)``
    state, summed over the netted lines (reusing :func:`scenarios.scenario_line_pnls` /
    :func:`scenarios.scenario_totals` — no second reprice path). An empty book reprices to a
    flat-zero surface over the configured axes (the honest full reprice of nothing); the axes
    are a pure function of config, independent of the book. ``steps`` is forwarded to the
    American lattice and ignored for European contracts.
    """
    spot_axis, vol_axis = surface_axes(config)
    grid = stress_surface_grid(config)
    cells = scenario_line_pnls(lines, grid, steps=steps)
    totals = scenario_totals(cells)  # scenario_id -> portfolio full-reprice PnL
    pnl_grid = tuple(
        tuple(totals.get(_surface_scenario_id(s, v), 0.0) for v in vol_axis) for s in spot_axis
    )
    return StressSurface(
        scenario_version=effective_surface_version(config),
        spot_axis=spot_axis,
        vol_axis=vol_axis,
        pnl_grid=pnl_grid,
    )
