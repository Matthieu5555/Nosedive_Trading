"""Step 12 — scenario stress: full reprice as truth, the local approximation, worst case.

Independent oracle (never the code under test): full-reprice PnL, worst case, and
the Taylor-approximation gaps for the pf-risk portfolio, derived by hand-coded
generalized Black-Scholes-Merton cross-checked against QuantLib's ``blackFormula``
(agreement ~1e-14). Market state and portfolio as in ``test_risk.py``. Shock
conventions: spot relative (new = spot*(1+shock)), vol additive, time a roll-down
in years; the full-reprice numbers are convention-independent and authoritative.
"""

from __future__ import annotations

import pytest
from algotrading.core.config import ScenarioConfig
from algotrading.infra.risk import (
    PositionRisk,
    Scenario,
    local_approx_pnl,
    local_approx_pnl_fd,
    position_risk,
    scenario_grid,
    scenario_line_pnls,
    worst_case,
)
from algotrading.infra.risk import greeks as greeks_mod
from algotrading.infra.risk import scenarios as scenario_mod
from fixtures.positions import RISK_VALUATIONS, risk_positions

# Explicit oracle scenarios (id, spot_shock, vol_shock, time_shock).
S1 = Scenario("spot_down_5", "spot", -0.05, 0.0, 0.0)
S2 = Scenario("spot_up_5", "spot", 0.05, 0.0, 0.0)
S3 = Scenario("vol_up_5", "vol", 0.0, 0.05, 0.0)
S4 = Scenario("vol_down_5", "vol", 0.0, -0.05, 0.0)
S5 = Scenario("crash", "combined", -0.05, 0.05, 0.0)
S6 = Scenario("roll_1d", "time", 0.0, 0.0, 1.0 / 365.0)
S7 = Scenario("spot_down_25", "spot", -0.25, 0.0, 0.0)

# Oracle portfolio full-reprice totals (USD).
ORACLE_TOTAL = {
    "spot_down_5": -3880.814974,
    "spot_up_5": 4628.317331,
    "vol_up_5": 768.450732,
    "vol_down_5": -752.043147,
    "crash": -3250.215153,
    "roll_1d": -16.465062,
    "spot_down_25": -14959.182145,
}
# Oracle per-line full-reprice PnL for the worst case S1 (contract_key -> PnL).
ORACLE_S1_LINES = {
    "AAPL|OPT|C|100": -2078.700940,
    "AAPL|OPT|P|100": -1435.649530,
    "AAPL|OPT|C|105": -366.464504,
}


def pf_lines() -> list[PositionRisk]:
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


def total_full_reprice(lines: list[PositionRisk], scenario: Scenario) -> float:
    cells = scenario_line_pnls(lines, [scenario])
    return sum(c.full_reprice_pnl for c in cells)


# --- Full reprice is the source of truth -------------------------------------
@pytest.mark.parametrize("scenario", [S1, S2, S3, S4, S5, S6, S7])
def test_full_reprice_total_matches_oracle(scenario: Scenario) -> None:
    got = total_full_reprice(pf_lines(), scenario)
    assert got == pytest.approx(ORACLE_TOTAL[scenario.scenario_id], rel=1e-6, abs=1e-3)


def test_per_line_full_reprice_matches_oracle_for_worst_case() -> None:
    cells = scenario_line_pnls(pf_lines(), [S1])
    by_key = {c.line.contract_key: c.full_reprice_pnl for c in cells}
    for key, expected in ORACLE_S1_LINES.items():
        assert by_key[key] == pytest.approx(expected, rel=1e-6, abs=1e-3)


# --- Worst case matches a hand-worked portfolio ------------------------------
def test_worst_case_is_spot_down_5_with_ranked_contributors() -> None:
    grid = [S1, S2, S3, S4, S5, S6]
    cells = scenario_line_pnls(pf_lines(), grid)
    wc = worst_case(cells)
    assert wc.scenario.scenario_id == "spot_down_5"
    assert wc.total_pnl == pytest.approx(ORACLE_TOTAL["spot_down_5"], rel=1e-6, abs=1e-3)
    # Contributors are worst-first: C100 (-2078.70), then P100 (-1435.65), then C105.
    assert [c.line.contract_key for c in wc.contributors] == [
        "AAPL|OPT|C|100",
        "AAPL|OPT|P|100",
        "AAPL|OPT|C|105",
    ]


def test_worst_case_over_no_cells_is_an_error_not_a_zero() -> None:
    with pytest.raises(ValueError):
        worst_case([])


# --- Local approximation: agrees small, diverges large -----------------------
@pytest.mark.parametrize("scenario", [S1, S2, S3, S4, S6])
def test_local_approx_agrees_with_full_reprice_for_small_shocks(scenario: Scenario) -> None:
    lines = pf_lines()
    full = total_full_reprice(lines, scenario)
    approx = sum(local_approx_pnl(line, scenario) for line in lines)
    assert approx == pytest.approx(full, rel=5e-2)  # oracle: max small-shock gap 1.4e-2


def test_local_approx_diverges_for_a_large_down_shock() -> None:
    # A large adverse (down) spot shock diverges: oracle rel gap 0.2154, ~15x the
    # small-shock tolerance. Note the divergence is direction-dependent — gamma
    # curvature keeps the Taylor approx closer for an up-move of the same size — so
    # this asserts the adverse case that matters for risk, not symmetric divergence.
    lines = pf_lines()
    full = total_full_reprice(lines, S7)
    approx = sum(local_approx_pnl(line, S7) for line in lines)
    rel_gap = abs(approx - full) / abs(full)
    assert rel_gap > 1e-1


# --- Grid construction is deterministic and complete -------------------------
def test_scenario_grid_families_ids_and_ordering_are_fixed() -> None:
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05))
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    assert ids == [
        "spot_-0.0500",
        "spot_+0.0500",
        "vol_+0.0500",
        "vol_-0.0500",
        "crash_spot-0.0500_vol+0.0500",  # most adverse spot + largest vol spike
        "roll_1d",
    ]
    # The grid is a pure function of the config: building it twice is identical.
    assert scenario_grid(config) == grid


def test_every_configured_scenario_executes_with_no_missing_cells() -> None:
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05,))
    grid = scenario_grid(config)
    lines = pf_lines()
    cells = scenario_line_pnls(lines, grid)
    # Completeness: result count equals grid size times position count, exactly.
    assert len(cells) == len(grid) * len(lines)
    # Every (scenario, contract) pair is present once.
    pairs = {(c.scenario.scenario_id, c.line.contract_key) for c in cells}
    assert len(pairs) == len(cells)


# --- The one shared bump source (the gotcha, made a test) --------------------
def test_greeks_and_scenario_engine_share_one_versioned_bump_source() -> None:
    # The Greeks cross-check and the scenario engine's finite-difference path must
    # draw their bump from the SAME versioned object, or risk and scenarios diverge
    # for non-economic reasons. Assert it is literally one object, not two.
    assert greeks_mod.DEFAULT_BUMPS is scenario_mod.DEFAULT_BUMPS
    assert greeks_mod.DEFAULT_BUMPS.version == scenario_mod.DEFAULT_BUMPS.version


def test_finite_difference_local_approx_matches_the_analytic_one() -> None:
    # The FD-greeks local approximation (which uses the shared bump) reproduces the
    # analytic-greeks one — concrete evidence the two paths share the same bump and
    # do not diverge, not merely that they reference the same object.
    line = pf_lines()[0]
    analytic = local_approx_pnl(line, S1)
    fd = local_approx_pnl_fd(line.valuation, quantity=line.quantity, scenario=S1)
    assert fd == pytest.approx(analytic, rel=1e-5)


def test_grid_without_shocks_has_only_the_time_roll() -> None:
    # With no spot or vol shocks configured, there is no combined crash to build.
    grid = scenario_grid(ScenarioConfig(version="scn-1", spot_shocks=(), vol_shocks=()))
    assert [s.scenario_id for s in grid] == ["roll_1d"]


def test_duplicate_configured_shocks_do_not_collapse_or_double_count_cells() -> None:
    # A duplicate shock must not mint a duplicate scenario id: that would silently
    # collapse cells in an id-keyed map and double-count the scenario in the total.
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05, -0.05, 0.05), vol_shocks=(0.05,))
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    assert len(ids) == len(set(ids))  # the duplicate -0.05 collapsed to one scenario
    lines = pf_lines()
    cells = scenario_line_pnls(lines, grid)
    pairs = {(c.scenario.scenario_id, c.line.contract_key) for c in cells}
    assert len(pairs) == len(cells)  # no missing and no duplicate cells
    # The worst case is the true single-counted loss, not 2x.
    wc = worst_case(cells)
    assert wc.scenario.scenario_id == "spot_-0.0500"
    assert wc.total_pnl == pytest.approx(ORACLE_TOTAL["spot_down_5"], rel=1e-6, abs=1e-3)
    assert len(wc.contributors) == len(lines)  # 3, not 6


def test_persisted_scenario_version_moves_with_grid_construction_constants() -> None:
    # The headline reproducibility guarantee: changing a grid-construction constant
    # (the roll-down set or the crash rule) must move the persisted version, so two
    # different grids can never share one scenario_version.
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,))
    version = scenario_mod.effective_scenario_version(config)
    assert version.startswith("scn-1+")  # carries the config section version
    assert scenario_mod._grid_construction_hash(roll_down_days=(1,)) != (
        scenario_mod._grid_construction_hash(roll_down_days=(1, 7))
    )
    assert scenario_mod._grid_construction_hash(crash_rule_tag="other") != (
        scenario_mod._grid_construction_hash(crash_rule_tag="crash=min_spot+max_vol")
    )
