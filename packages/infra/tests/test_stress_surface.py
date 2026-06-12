"""WS 2B — stress surface: the full cartesian (spot × vol) full-reprice PnL surface.

Independent oracle (never the code under test): each cell's PnL is recomputed by repricing
the book directly through the pricing engine at the shocked state (``new_spot = spot*(1+s)``,
``new_vol = vol+v``) and differencing against base — a path that shares none of
``stress_surface``'s grid-construction or z-grid-arrangement code (the logic under test). The
pricer itself is independently validated in ``test_scenario.py`` against a BSM/QuantLib oracle.

Market state and portfolio are the curated ``fixtures.positions`` book (spot 100, T 0.25,
vol 0.20, mult 100), the same the scenario oracles were derived against.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest
from algotrading.core.config import (
    ConfigFieldError,
    ScenarioConfig,
    StressSurfaceConfig,
    load_platform_config,
)
from algotrading.infra.pricing import price

# Import from submodules, not the risk package __init__ — WS 2C is concurrently editing that
# __init__, so this keeps 2B off a file under another active claim.
from algotrading.infra.risk.greeks import PositionRisk, position_risk
from algotrading.infra.risk.scenarios import ScenarioGridError, effective_scenario_version
from algotrading.infra.risk.stress_surface import (
    StressSurface,
    effective_surface_version,
    stress_surface,
    stress_surface_grid,
    surface_axes,
)
from algotrading.infra.risk.valuation import pricing_state_for
from fixtures.positions import RISK_VALUATIONS, risk_positions

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIGS_DIR = _REPO_ROOT / "configs"
_ENGINE_SOURCE = (
    _REPO_ROOT
    / "packages/infra/src/algotrading/infra/risk/stress_surface.py"
)


# --- fixtures / helpers ------------------------------------------------------
def pf_lines() -> list[PositionRisk]:
    """The pf-risk book (long 10 C100, short 5 P100, long 3 C105) as priced risk lines."""
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


def call_line(quantity: float = 1.0) -> list[PositionRisk]:
    """A single long-call leg, for sign-of-PnL convention checks."""
    return [
        position_risk(
            portfolio_id="pf-1leg",
            quantity=quantity,
            valuation=RISK_VALUATIONS["AAPL|OPT|C|100"],
        )
    ]


def _scenario_config(
    *,
    spot_abs: float,
    vol_abs: float,
    spot_steps: int,
    vol_steps: int,
    version: str = "sc-test",
    ss_version: str = "ss-test",
) -> ScenarioConfig:
    return ScenarioConfig(
        version=version,
        spot_shocks=(-0.05, 0.05),
        vol_shocks=(0.05,),
        stress_surface=StressSurfaceConfig(
            version=ss_version,
            spot_shock_abs=spot_abs,
            vol_shock_abs=vol_abs,
            spot_steps=spot_steps,
            vol_steps=vol_steps,
        ),
    )


def oracle_cell_pnl(lines: list[PositionRisk], spot_shock: float, vol_shock: float) -> float:
    """Independent full reprice of the book under one (spot, vol) shock — the oracle.

    Reprices each leg directly through the pricing engine at the shocked state and differences
    against its base price; sums over legs. Shares no code with ``stress_surface``'s grid.
    """
    total = 0.0
    for line in lines:
        val = line.valuation
        shocked = dataclasses.replace(
            val, spot=val.spot * (1.0 + spot_shock), volatility=max(val.volatility + vol_shock, 0.0)
        )
        shocked_price = price(pricing_state_for(shocked)).price
        total += (shocked_price - line.greeks.price) * line.scale
    return total


def _centre_index(axis: tuple[float, ...]) -> int:
    return axis.index(0.0)


# --- the acceptance criterion: surface == an independent full reprice --------
def test_stress_surface_matches_full_reprice() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.3, spot_steps=5, vol_steps=3)
    lines = pf_lines()
    surface = stress_surface(lines, config)

    assert isinstance(surface, StressSurface)
    assert len(surface.spot_axis) == 5
    assert len(surface.vol_axis) == 3
    assert len(surface.pnl_grid) == 5
    assert all(len(row) == 3 for row in surface.pnl_grid)

    for i, s in enumerate(surface.spot_axis):
        for j, v in enumerate(surface.vol_axis):
            assert surface.pnl_grid[i][j] == pytest.approx(
                oracle_cell_pnl(lines, s, v), rel=1e-9, abs=1e-6
            )
    # The centre cell (0 spot, 0 vol) is ≈ 0 PnL by construction.
    ci, cj = _centre_index(surface.spot_axis), _centre_index(surface.vol_axis)
    assert surface.pnl_grid[ci][cj] == pytest.approx(0.0, abs=1e-6)


# --- cartesian completeness --------------------------------------------------
def test_stress_grid_is_the_full_cartesian_product() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=9, vol_steps=9)
    grid = stress_surface_grid(config)
    assert len(grid) == 9 * 9
    pairs = {(round(g.spot_shock, 6), round(g.vol_shock, 6)) for g in grid}
    assert len(pairs) == 81  # every (spot, vol) pair present exactly once
    assert all(g.time_shock == 0.0 for g in grid)  # the surface stresses the snapshot only
    assert all(g.family == "surface" for g in grid)
    # A pure function of config: building it twice is identical.
    assert stress_surface_grid(config) == grid


# --- config-driven ranges (ADR 0028) ----------------------------------------
def test_stress_range_is_config_driven() -> None:
    config = load_platform_config(_CONFIGS_DIR)
    ss = config.scenario.stress_surface
    # The shipped production grid is ±50%/±50% on 9×9 — from scenarios.yaml, not a literal.
    assert ss.spot_shock_abs == 0.5
    assert ss.vol_shock_abs == 0.5
    assert ss.spot_steps == 9
    assert ss.vol_steps == 9
    spot_axis, vol_axis = surface_axes(config.scenario)
    assert len(spot_axis) == 9 and len(vol_axis) == 9
    assert 0.0 in spot_axis and 0.0 in vol_axis
    assert min(spot_axis) == pytest.approx(-0.5) and max(spot_axis) == pytest.approx(0.5)
    # A YAML-equivalent edit (range/steps) changes the axes and the cell count with no code
    # change — the construction is a pure function of the config.
    narrowed = config.scenario.model_copy(
        update={"stress_surface": ss.model_copy(update={"spot_steps": 3, "spot_shock_abs": 0.2})},
    )
    n_spot, n_vol = surface_axes(narrowed)
    assert len(n_spot) == 3 and max(n_spot) == pytest.approx(0.2)
    assert len(stress_surface_grid(narrowed)) == 3 * 9


def test_no_production_stress_literal_in_engine_source() -> None:
    # ADR 0028: the ±50%/9 grid is config, never a literal in the grid builder. A grep guard
    # so a future edit cannot quietly re-hardcode the range/steps back into the engine.
    src = _ENGINE_SOURCE.read_text(encoding="utf-8")
    assert "0.5" not in src, "the ±50% magnitude must come from scenarios.yaml, not a literal"
    assert "spot_steps=9" not in src and "vol_steps=9" not in src


# --- shock conventions hold --------------------------------------------------
def test_shock_conventions_hold() -> None:
    # A long ATM call: a positive spot shock (relative) raises PnL, a negative one lowers it;
    # a positive vol shock (additive, vega > 0) raises PnL. Direction is what we assert.
    config = _scenario_config(spot_abs=0.5, vol_abs=0.3, spot_steps=3, vol_steps=3)
    lines = call_line(quantity=1.0)
    surface = stress_surface(lines, config)
    ci = _centre_index(surface.spot_axis)
    cj = _centre_index(surface.vol_axis)
    # spot axis sorted ascending [-abs, 0, +abs]; at centre vol, up > flat > down.
    assert surface.pnl_grid[-1][cj] > surface.pnl_grid[ci][cj] > surface.pnl_grid[0][cj]
    # vol axis: at centre spot, +vol > flat > -vol (long vega).
    assert surface.pnl_grid[ci][-1] > surface.pnl_grid[ci][cj] > surface.pnl_grid[ci][0]


# --- version moves with the stress block (regenerability) --------------------
def test_surface_version_moves_with_stress_block() -> None:
    base = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=9, vol_steps=9, ss_version="ss-a")
    v0 = effective_surface_version(base)
    # Carries the scenario version (a strict superset of effective_scenario_version).
    assert v0.startswith(effective_scenario_version(base))

    def moved(**ss_changes: object) -> str:
        return effective_surface_version(
            base.model_copy(
                update={"stress_surface": base.stress_surface.model_copy(update=ss_changes)}
            )
        )

    versions = {
        v0,
        moved(spot_shock_abs=0.4),
        moved(vol_shock_abs=0.4),
        moved(spot_steps=7),
        moved(vol_steps=7),
        moved(version="ss-b"),
    }
    assert len(versions) == 6  # every stress-block edit moves the version


def test_surface_version_is_stable_across_processes() -> None:
    # No PYTHONHASHSEED dependence (TESTING.md cross-process requirement): SHA-256 over
    # canonical JSON, never Python's salted hash().
    expected = effective_surface_version(load_platform_config(_CONFIGS_DIR).scenario)
    code = (
        "from algotrading.core.config import load_platform_config;"
        "from algotrading.infra.risk.stress_surface import effective_surface_version;"
        "print(effective_surface_version(load_platform_config('configs').scenario))"
    )
    for seed in ("0", "7", "98765"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=_REPO_ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == expected, f"surface version drifted under seed {seed}"


# --- edge cases (the floor) --------------------------------------------------
def test_empty_book_is_a_flat_zero_surface() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=5, vol_steps=5)
    surface = stress_surface([], config)
    assert len(surface.spot_axis) == 5 and len(surface.vol_axis) == 5  # axes are config-driven
    assert all(cell == 0.0 for row in surface.pnl_grid for cell in row)  # nothing to reprice


def test_single_leg_book_is_a_valid_surface_with_zero_centre() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.3, spot_steps=5, vol_steps=3)
    lines = call_line(quantity=2.0)
    surface = stress_surface(lines, config)
    for i, s in enumerate(surface.spot_axis):
        for j, v in enumerate(surface.vol_axis):
            assert surface.pnl_grid[i][j] == pytest.approx(
                oracle_cell_pnl(lines, s, v), rel=1e-9, abs=1e-6
            )
    ci, cj = _centre_index(surface.spot_axis), _centre_index(surface.vol_axis)
    assert surface.pnl_grid[ci][cj] == pytest.approx(0.0, abs=1e-6)


def test_mixed_european_and_american_book_matches_full_reprice() -> None:
    # The vectorized European path and the scalar American (lattice) path add cell-by-cell:
    # a book holding one of each must still match the independent oracle everywhere. The
    # oracle reprices both legs through the engine (dispatching on style), sharing no code
    # with the surface's European/American partition.
    config = _scenario_config(spot_abs=0.4, vol_abs=0.3, spot_steps=5, vol_steps=3)
    american_val = dataclasses.replace(
        RISK_VALUATIONS["AAPL|OPT|C|100"],
        contract_key="AAPL|OPT|C|100|AM",
        exercise_style="american",
    )
    lines = [
        position_risk(portfolio_id="mix", quantity=4.0, valuation=RISK_VALUATIONS["AAPL|OPT|C|100"]),
        position_risk(portfolio_id="mix", quantity=-2.0, valuation=american_val),
    ]
    surface = stress_surface(lines, config)
    for i, s in enumerate(surface.spot_axis):
        for j, v in enumerate(surface.vol_axis):
            assert surface.pnl_grid[i][j] == pytest.approx(
                oracle_cell_pnl(lines, s, v), rel=1e-9, abs=1e-6
            )


def test_degenerate_zero_width_range_is_the_centre_column_only() -> None:
    config = _scenario_config(spot_abs=0.0, vol_abs=0.0, spot_steps=9, vol_steps=9)
    spot_axis, vol_axis = surface_axes(config)
    assert spot_axis == (0.0,) and vol_axis == (0.0,)  # collapses to the centre cell
    surface = stress_surface(pf_lines(), config)
    assert surface.pnl_grid == ((pytest.approx(0.0, abs=1e-6),),)


def test_single_step_axis_is_the_centre_cell() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=1, vol_steps=1)
    spot_axis, vol_axis = surface_axes(config)
    assert spot_axis == (0.0,) and vol_axis == (0.0,)


# --- config validation rejects malformed stress blocks -----------------------
def test_even_step_count_is_rejected() -> None:
    with pytest.raises(ConfigFieldError):
        StressSurfaceConfig(version="ss", spot_steps=4)


def test_non_positive_step_count_is_rejected() -> None:
    with pytest.raises(ConfigFieldError):
        StressSurfaceConfig(version="ss", vol_steps=0)


def test_negative_magnitude_is_rejected() -> None:
    with pytest.raises(ConfigFieldError):
        StressSurfaceConfig(version="ss", spot_shock_abs=-0.1)


def test_non_finite_magnitude_is_rejected() -> None:
    with pytest.raises(ConfigFieldError):
        StressSurfaceConfig(version="ss", vol_shock_abs=float("nan"))


def test_empty_version_is_rejected() -> None:
    with pytest.raises(ConfigFieldError):
        StressSurfaceConfig(version="")


def test_colliding_axis_ids_raise_not_silently_collapse() -> None:
    # Two distinct axis points that format to the same 4-dp scenario id would collapse a
    # surface cell; the grid builder guards it loudly. A ±1e-5 magnitude puts +1e-5 and the
    # centre 0.0 both at "+0.0000", a genuine precision collision.
    config = _scenario_config(spot_abs=1e-5, vol_abs=0.5, spot_steps=3, vol_steps=1)
    with pytest.raises(ScenarioGridError):
        stress_surface_grid(config)
