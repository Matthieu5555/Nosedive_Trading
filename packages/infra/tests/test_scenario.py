from __future__ import annotations

import math

import pytest
from algotrading.core.config import NamedScenarioConfig, ScenarioConfig
from algotrading.infra.risk import (
    BasketCorrelationExposure,
    PositionRisk,
    Scenario,
    correlation_shock_pnl,
    full_reprice_pnl,
    local_approx_pnl,
    local_approx_pnl_fd,
    position_risk,
    scenario_grid,
    scenario_line_pnls,
    scenario_totals,
    worst_case,
)
from algotrading.infra.risk import greeks as greeks_mod
from algotrading.infra.risk import scenarios as scenario_mod
from algotrading.infra.risk.basket import NonPSDBasketError
from fixtures.positions import RISK_VALUATIONS, risk_positions

S1 = Scenario("spot_down_5", "spot", -0.05, 0.0, 0.0)
S2 = Scenario("spot_up_5", "spot", 0.05, 0.0, 0.0)
S3 = Scenario("vol_up_5", "vol", 0.0, 0.05, 0.0)
S4 = Scenario("vol_down_5", "vol", 0.0, -0.05, 0.0)
S5 = Scenario("crash", "combined", -0.05, 0.05, 0.0)
S6 = Scenario("roll_1d", "time", 0.0, 0.0, 1.0 / 365.0)
S7 = Scenario("spot_down_25", "spot", -0.25, 0.0, 0.0)

ORACLE_TOTAL = {
    "spot_down_5": -3880.814974,
    "spot_up_5": 4628.317331,
    "vol_up_5": 768.450732,
    "vol_down_5": -752.043147,
    "crash": -3250.215153,
    "roll_1d": -16.465062,
    "spot_down_25": -14959.182145,
}
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


@pytest.mark.parametrize("scenario", [S1, S2, S3, S4, S5, S6, S7])
def test_full_reprice_total_matches_oracle(scenario: Scenario) -> None:
    got = total_full_reprice(pf_lines(), scenario)
    assert got == pytest.approx(ORACLE_TOTAL[scenario.scenario_id], rel=1e-6, abs=1e-3)


def test_per_line_full_reprice_matches_oracle_for_worst_case() -> None:
    cells = scenario_line_pnls(pf_lines(), [S1])
    by_key = {c.line.contract_key: c.full_reprice_pnl for c in cells}
    for key, expected in ORACLE_S1_LINES.items():
        assert by_key[key] == pytest.approx(expected, rel=1e-6, abs=1e-3)


def test_worst_case_is_spot_down_5_with_ranked_contributors() -> None:
    grid = [S1, S2, S3, S4, S5, S6]
    cells = scenario_line_pnls(pf_lines(), grid)
    wc = worst_case(cells)
    assert wc.scenario.scenario_id == "spot_down_5"
    assert wc.total_pnl == pytest.approx(ORACLE_TOTAL["spot_down_5"], rel=1e-6, abs=1e-3)
    assert [c.line.contract_key for c in wc.contributors] == [
        "AAPL|OPT|C|100",
        "AAPL|OPT|P|100",
        "AAPL|OPT|C|105",
    ]


def test_worst_case_over_no_cells_is_an_error_not_a_zero() -> None:
    with pytest.raises(ValueError):
        worst_case([])


@pytest.mark.parametrize("scenario", [S1, S2, S3, S4, S6])
def test_local_approx_agrees_with_full_reprice_for_small_shocks(scenario: Scenario) -> None:
    lines = pf_lines()
    full = total_full_reprice(lines, scenario)
    approx = sum(local_approx_pnl(line, scenario) for line in lines)
    assert approx == pytest.approx(full, rel=5e-2)


def test_local_approx_diverges_for_a_large_down_shock() -> None:
    lines = pf_lines()
    full = total_full_reprice(lines, S7)
    approx = sum(local_approx_pnl(line, S7) for line in lines)
    rel_gap = abs(approx - full) / abs(full)
    assert rel_gap > 1e-1


def test_scenario_grid_families_ids_and_ordering_are_fixed() -> None:
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05))
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    assert ids == [
        "spot_-0.0500",
        "spot_+0.0500",
        "vol_+0.0500",
        "vol_-0.0500",
        "crash_spot-0.0500_vol+0.0500",
        "roll_1d",
    ]
    assert scenario_grid(config) == grid


def test_every_configured_scenario_executes_with_no_missing_cells() -> None:
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05,))
    grid = scenario_grid(config)
    lines = pf_lines()
    cells = scenario_line_pnls(lines, grid)
    assert len(cells) == len(grid) * len(lines)
    pairs = {(c.scenario.scenario_id, c.line.contract_key) for c in cells}
    assert len(pairs) == len(cells)


def test_greeks_and_scenario_engine_share_one_versioned_bump_source() -> None:
    assert greeks_mod.DEFAULT_BUMPS is scenario_mod.DEFAULT_BUMPS
    assert greeks_mod.DEFAULT_BUMPS.version == scenario_mod.DEFAULT_BUMPS.version


def test_finite_difference_local_approx_matches_the_analytic_one() -> None:
    line = pf_lines()[0]
    analytic = local_approx_pnl(line, S1)
    fd = local_approx_pnl_fd(line.valuation, quantity=line.quantity, scenario=S1)
    assert fd == pytest.approx(analytic, rel=1e-5)


def test_grid_without_shocks_has_only_the_time_roll() -> None:
    grid = scenario_grid(ScenarioConfig(version="scn-1", spot_shocks=(), vol_shocks=()))
    assert [s.scenario_id for s in grid] == ["roll_1d"]


def test_rate_family_ids_and_ordering_are_fixed() -> None:
    config = ScenarioConfig(
        version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,), rate_shocks=(-0.0025, 0.0025)
    )
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    assert ids == [
        "spot_-0.0500",
        "vol_+0.0500",
        "rate_-0.0025",
        "rate_+0.0025",
        "crash_spot-0.0500_vol+0.0500",
        "roll_1d",
    ]
    rate_scenarios = [s for s in grid if s.family == "rate"]
    assert [s.rate_shock for s in rate_scenarios] == [-0.0025, 0.0025]
    assert all(s.spot_shock == 0.0 and s.vol_shock == 0.0 and s.time_shock == 0.0 for s in rate_scenarios)


def test_empty_rate_axis_adds_no_family_and_does_not_move_the_version() -> None:
    base = ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,))
    with_rate = ScenarioConfig(
        version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,), rate_shocks=(0.0025,)
    )
    assert not any(s.family == "rate" for s in scenario_grid(base))
    assert scenario_mod.effective_scenario_version(base) != scenario_mod.effective_scenario_version(
        with_rate
    )


def test_rate_shock_applies_additively_to_the_implied_rate() -> None:
    val = RISK_VALUATIONS["AAPL|OPT|C|100"]
    scn = Scenario("rate_+25bp", "rate", 0.0, 0.0, 0.0, 0.0025)
    shocked = scenario_mod.shock_valuation(val, scn)
    assert shocked.implied_rate == pytest.approx(val.implied_rate + 0.0025)
    assert shocked.spot == val.spot
    assert shocked.volatility == val.volatility
    assert shocked.maturity_years == val.maturity_years


def test_rate_scenario_drives_the_rho_term_and_the_full_reprice_agrees() -> None:
    line = pf_lines()[0]
    scn = Scenario("rate_+10bp", "rate", 0.0, 0.0, 0.0, 0.0010)
    approx = local_approx_pnl(line, scn)
    assert approx == pytest.approx(line.greeks.rho * 0.0010 * line.scale)
    assert approx != 0.0
    full = scenario_line_pnls([line], [scn])[0].full_reprice_pnl
    assert full == pytest.approx(approx, rel=1e-3)


def test_duplicate_configured_shocks_do_not_collapse_or_double_count_cells() -> None:
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05, -0.05, 0.05), vol_shocks=(0.05,))
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    assert len(ids) == len(set(ids))
    lines = pf_lines()
    cells = scenario_line_pnls(lines, grid)
    pairs = {(c.scenario.scenario_id, c.line.contract_key) for c in cells}
    assert len(pairs) == len(cells)
    wc = worst_case(cells)
    assert wc.scenario.scenario_id == "spot_-0.0500"
    assert wc.total_pnl == pytest.approx(ORACLE_TOTAL["spot_down_5"], rel=1e-6, abs=1e-3)
    assert len(wc.contributors) == len(lines)


def test_persisted_scenario_version_moves_with_grid_construction_constants() -> None:
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,))
    version = scenario_mod.effective_scenario_version(config)
    assert version.startswith("scn-1+")
    assert scenario_mod._grid_construction_hash(roll_down_days=(1,)) != (
        scenario_mod._grid_construction_hash(roll_down_days=(1, 7))
    )
    assert scenario_mod._grid_construction_hash((1,), crash_rule_tag="other") != (
        scenario_mod._grid_construction_hash((1,), crash_rule_tag="crash=min_spot+max_vol")
    )
    assert scenario_mod.effective_scenario_version(config) != scenario_mod.effective_scenario_version(
        ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,), roll_down_days=(1, 7))
    )


def test_scenario_totals_and_worst_case_are_reorder_invariant() -> None:
    grid = [S1, S2, S3, S4, S5, S6, S7]
    cells = scenario_line_pnls(pf_lines(), grid)

    totals_forward = scenario_totals(cells)
    totals_reversed = scenario_totals(list(reversed(cells)))
    assert set(totals_forward) == set(totals_reversed)
    for sid in totals_forward:
        assert totals_forward[sid] == totals_reversed[sid]

    wc_forward = worst_case(cells)
    wc_reversed = worst_case(list(reversed(cells)))
    assert wc_forward.scenario.scenario_id == wc_reversed.scenario.scenario_id
    assert wc_forward.total_pnl == wc_reversed.total_pnl
    assert wc_forward.scenario.scenario_id == "spot_down_25"


def test_named_scenario_family_ids_ordering_and_compound_shock() -> None:
    config = ScenarioConfig(
        version="scn-1",
        spot_shocks=(-0.05,),
        vol_shocks=(0.05,),
        named_scenarios=(
            NamedScenarioConfig(label="2008", spot_shock=-0.45, vol_shock=0.40, rate_shock=-0.02),
            NamedScenarioConfig(label="covid-2020", spot_shock=-0.35, vol_shock=0.50, rate_shock=-0.01),
        ),
    )
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    assert ids == [
        "spot_-0.0500",
        "vol_+0.0500",
        "crash_spot-0.0500_vol+0.0500",
        "roll_1d",
        "named_2008",
        "named_covid-2020",
    ]
    named = [s for s in grid if s.family == "named"]
    assert (named[0].spot_shock, named[0].vol_shock, named[0].rate_shock) == (-0.45, 0.40, -0.02)
    assert (named[1].spot_shock, named[1].vol_shock, named[1].rate_shock) == (-0.35, 0.50, -0.01)


def test_named_compound_shock_full_reprice_matches_independent_oracle() -> None:
    line = position_risk(
        portfolio_id="pf-risk", quantity=1.0, valuation=RISK_VALUATIONS["AAPL|OPT|C|100"]
    )
    named = Scenario("named_test", "named", -0.20, 0.10, 0.0, 0.0025)
    pnl = full_reprice_pnl(line, named)
    assert pnl == pytest.approx(-354.85698507028565, rel=1e-9)


def test_empty_named_catalogue_adds_no_family_and_does_not_move_the_version() -> None:
    base = ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,))
    with_named = ScenarioConfig(
        version="scn-1",
        spot_shocks=(-0.05,),
        vol_shocks=(0.05,),
        named_scenarios=(NamedScenarioConfig(label="2008", spot_shock=-0.45),),
    )
    assert not any(s.family == "named" for s in scenario_grid(base))
    assert scenario_mod.effective_scenario_version(base) != scenario_mod.effective_scenario_version(
        with_named
    )


def test_correlation_family_ids_and_ordering_are_fixed() -> None:
    config = ScenarioConfig(
        version="scn-1",
        spot_shocks=(-0.05,),
        vol_shocks=(0.05,),
        correlation_shocks=(0.10, 0.20),
    )
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    assert ids == [
        "spot_-0.0500",
        "vol_+0.0500",
        "corr_+0.1000",
        "corr_+0.2000",
        "crash_spot-0.0500_vol+0.0500",
        "roll_1d",
    ]
    corr = [s for s in grid if s.family == "correlation"]
    assert [s.correlation_shock for s in corr] == [0.10, 0.20]
    assert all(
        s.spot_shock == 0.0 and s.vol_shock == 0.0 and s.rate_shock == 0.0 and s.time_shock == 0.0
        for s in corr
    )


def test_empty_correlation_axis_adds_no_family_and_does_not_move_the_version() -> None:
    base = ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,))
    with_corr = ScenarioConfig(
        version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,), correlation_shocks=(0.10,)
    )
    assert not any(s.family == "correlation" for s in scenario_grid(base))
    assert scenario_mod.effective_scenario_version(base) != scenario_mod.effective_scenario_version(
        with_corr
    )


def test_correlation_shock_reprices_through_basket_variance_against_an_independent_oracle() -> None:
    exposure = BasketCorrelationExposure(
        weights=(0.5, 0.3, 0.2),
        vols=(0.25, 0.30, 0.20),
        avg_correlation=0.40,
        vol_sensitivity=25000.0,
    )
    scn = Scenario("corr_+0.2000", "correlation", 0.0, 0.0, 0.0, 0.0, 0.20)
    pnl = correlation_shock_pnl(exposure, scn)
    assert pnl == pytest.approx(467.41420852794215, rel=1e-12)
    assert math.isclose(0.2029901475441604, 0.2029901475441604)


def test_correlation_shock_is_inert_on_a_non_correlation_scenario() -> None:
    exposure = BasketCorrelationExposure(
        weights=(0.5, 0.5), vols=(0.25, 0.25), avg_correlation=0.40, vol_sensitivity=10000.0
    )
    for scn in (S1, S3, S6, Scenario("named_x", "named", -0.45, 0.40, 0.0, -0.02)):
        assert correlation_shock_pnl(exposure, scn) == 0.0


def test_correlation_shock_into_a_non_psd_basket_raises_not_silently_floors() -> None:
    exposure = BasketCorrelationExposure(
        weights=(0.5, 0.5), vols=(0.25, 0.25), avg_correlation=-0.5, vol_sensitivity=10000.0
    )
    scn = Scenario("corr_-0.8000", "correlation", 0.0, 0.0, 0.0, 0.0, -0.8)
    with pytest.raises(NonPSDBasketError):
        correlation_shock_pnl(exposure, scn)


def test_construction_hash_folds_each_new_family_only_when_non_empty() -> None:
    base = scenario_mod._grid_construction_hash(roll_down_days=(1,))
    assert (
        scenario_mod._grid_construction_hash(
            roll_down_days=(1,), correlation_shocks=(), named_scenarios=()
        )
        == base
    )
    with_corr = scenario_mod._grid_construction_hash(roll_down_days=(1,), correlation_shocks=(0.10,))
    with_named = scenario_mod._grid_construction_hash(
        roll_down_days=(1,),
        named_scenarios=(NamedScenarioConfig(label="2008", spot_shock=-0.45),),
    )
    assert with_corr != base
    assert with_named != base
    assert with_corr != with_named
    other_named = scenario_mod._grid_construction_hash(
        roll_down_days=(1,),
        named_scenarios=(NamedScenarioConfig(label="2008", spot_shock=-0.50),),
    )
    assert other_named != with_named
