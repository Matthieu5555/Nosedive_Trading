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


def pf_lines() -> list[PositionRisk]:
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


def call_line(quantity: float = 1.0) -> list[PositionRisk]:
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
    ci, cj = _centre_index(surface.spot_axis), _centre_index(surface.vol_axis)
    assert surface.pnl_grid[ci][cj] == pytest.approx(0.0, abs=1e-6)


def test_stress_grid_is_the_full_cartesian_product() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=9, vol_steps=9)
    grid = stress_surface_grid(config)
    assert len(grid) == 9 * 9
    pairs = {(round(g.spot_shock, 6), round(g.vol_shock, 6)) for g in grid}
    assert len(pairs) == 81
    assert all(g.time_shock == 0.0 for g in grid)
    assert all(g.family == "surface" for g in grid)
    assert stress_surface_grid(config) == grid


def test_stress_range_is_config_driven() -> None:
    config = load_platform_config(_CONFIGS_DIR)
    ss = config.scenario.stress_surface
    assert ss.spot_shock_abs == 0.5
    assert ss.vol_shock_abs == 0.5
    assert ss.spot_steps == 9
    assert ss.vol_steps == 9
    spot_axis, vol_axis = surface_axes(config.scenario)
    assert len(spot_axis) == 9 and len(vol_axis) == 9
    assert 0.0 in spot_axis and 0.0 in vol_axis
    assert min(spot_axis) == pytest.approx(-0.5) and max(spot_axis) == pytest.approx(0.5)
    narrowed = config.scenario.model_copy(
        update={"stress_surface": ss.model_copy(update={"spot_steps": 3, "spot_shock_abs": 0.2})},
    )
    n_spot, n_vol = surface_axes(narrowed)
    assert len(n_spot) == 3 and max(n_spot) == pytest.approx(0.2)
    assert len(stress_surface_grid(narrowed)) == 3 * 9


def test_no_production_stress_literal_in_engine_source() -> None:
    src = _ENGINE_SOURCE.read_text(encoding="utf-8")
    assert "0.5" not in src, "the ±50% magnitude must come from scenarios.yaml, not a literal"
    assert "spot_steps=9" not in src and "vol_steps=9" not in src


def test_shock_conventions_hold() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.3, spot_steps=3, vol_steps=3)
    lines = call_line(quantity=1.0)
    surface = stress_surface(lines, config)
    ci = _centre_index(surface.spot_axis)
    cj = _centre_index(surface.vol_axis)
    assert surface.pnl_grid[-1][cj] > surface.pnl_grid[ci][cj] > surface.pnl_grid[0][cj]
    assert surface.pnl_grid[ci][-1] > surface.pnl_grid[ci][cj] > surface.pnl_grid[ci][0]


def test_surface_version_moves_with_stress_block() -> None:
    base = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=9, vol_steps=9, ss_version="ss-a")
    v0 = effective_surface_version(base)
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
    assert len(versions) == 6


def test_surface_version_is_stable_across_processes() -> None:
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


def test_empty_book_is_a_flat_zero_surface() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=5, vol_steps=5)
    surface = stress_surface([], config)
    assert len(surface.spot_axis) == 5 and len(surface.vol_axis) == 5
    assert all(cell == 0.0 for row in surface.pnl_grid for cell in row)


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
    assert spot_axis == (0.0,) and vol_axis == (0.0,)
    surface = stress_surface(pf_lines(), config)
    assert surface.pnl_grid == ((pytest.approx(0.0, abs=1e-6),),)


def test_single_step_axis_is_the_centre_cell() -> None:
    config = _scenario_config(spot_abs=0.5, vol_abs=0.5, spot_steps=1, vol_steps=1)
    spot_axis, vol_axis = surface_axes(config)
    assert spot_axis == (0.0,) and vol_axis == (0.0,)


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
    config = _scenario_config(spot_abs=1e-5, vol_abs=0.5, spot_steps=3, vol_steps=1)
    with pytest.raises(ScenarioGridError):
        stress_surface_grid(config)
