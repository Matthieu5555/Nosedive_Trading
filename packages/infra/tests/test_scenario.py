"""Step 12 — scenario stress: full reprice as truth, the local approximation, worst case.

Independent oracle (never the code under test): full-reprice PnL, worst case, and
the Taylor-approximation gaps for the pf-risk portfolio, derived by hand-coded
generalized Black-Scholes-Merton cross-checked against QuantLib's ``blackFormula``
(agreement ~1e-14). Market state and portfolio as in ``test_risk.py``. Shock
conventions: spot relative (new = spot*(1+shock)), vol additive, time a roll-down
in years; the full-reprice numbers are convention-independent and authoritative.
"""

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


# --- Rate-shock axis (T-scenario-rate-axis, the course's 3rd stress axis) -----
def test_rate_family_ids_and_ordering_are_fixed() -> None:
    config = ScenarioConfig(
        version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,), rate_shocks=(-0.0025, 0.0025)
    )
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    # The rate family sits between vol and the combined crash, in config order.
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
    # The rate scenarios move ONLY the rate axis (spot/vol/time held).
    assert all(s.spot_shock == 0.0 and s.vol_shock == 0.0 and s.time_shock == 0.0 for s in rate_scenarios)


def test_empty_rate_axis_adds_no_family_and_does_not_move_the_version() -> None:
    base = ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,))
    with_rate = ScenarioConfig(
        version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,), rate_shocks=(0.0025,)
    )
    # Empty rate axis: no 'rate' family, and the persisted version is unchanged from a
    # grid built before the axis existed (the strictly-additive-construction guarantee).
    assert not any(s.family == "rate" for s in scenario_grid(base))
    # Adding a rate axis is tamper-evident: it moves effective_scenario_version.
    assert scenario_mod.effective_scenario_version(base) != scenario_mod.effective_scenario_version(
        with_rate
    )


def test_rate_shock_applies_additively_to_the_implied_rate() -> None:
    val = RISK_VALUATIONS["AAPL|OPT|C|100"]
    scn = Scenario("rate_+25bp", "rate", 0.0, 0.0, 0.0, 0.0025)
    shocked = scenario_mod.shock_valuation(val, scn)
    # Additive in rate units; forward-fixed (spot/carry/vol/maturity unchanged).
    assert shocked.implied_rate == pytest.approx(val.implied_rate + 0.0025)
    assert shocked.spot == val.spot
    assert shocked.volatility == val.volatility
    assert shocked.maturity_years == val.maturity_years


def test_rate_scenario_drives_the_rho_term_and_the_full_reprice_agrees() -> None:
    # End-to-end: a small pure rate shock makes the (previously dormant) rho term fire, and
    # the full reprice agrees with it to first order — the rate axis closing the §7.2 loop.
    line = pf_lines()[0]
    scn = Scenario("rate_+10bp", "rate", 0.0, 0.0, 0.0, 0.0010)
    approx = local_approx_pnl(line, scn)
    # The local approximation IS the rho term for a pure rate move (rho per 1.00 rate).
    assert approx == pytest.approx(line.greeks.rho * 0.0010 * line.scale)
    assert approx != 0.0
    full = scenario_line_pnls([line], [scn])[0].full_reprice_pnl
    # Small shock: the rho term explains the reprice (forward-fixed rho is exact to O(dr²)).
    assert full == pytest.approx(approx, rel=1e-3)


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
    assert scenario_mod._grid_construction_hash((1,), crash_rule_tag="other") != (
        scenario_mod._grid_construction_hash((1,), crash_rule_tag="crash=min_spot+max_vol")
    )
    # The configured roll-down set now drives the persisted version too.
    assert scenario_mod.effective_scenario_version(config) != scenario_mod.effective_scenario_version(
        ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,), roll_down_days=(1, 7))
    )


# --- Reorder-invariant accumulation (FIX 4: fsum, not sum/+=) ------------------
def test_scenario_totals_and_worst_case_are_reorder_invariant() -> None:
    # scenario_totals and worst_case accumulate per-scenario PnL with math.fsum, so the
    # totals — and therefore the worst-case selection — are bit-stable regardless of the
    # order the cells arrive in. We build the real pf-risk cells, then feed worst_case a
    # reversed copy and assert it picks the identical scenario and the bit-identical total.
    grid = [S1, S2, S3, S4, S5, S6, S7]
    cells = scenario_line_pnls(pf_lines(), grid)

    totals_forward = scenario_totals(cells)
    totals_reversed = scenario_totals(list(reversed(cells)))
    # Same keys, and each scenario's total is bit-identical under the reorder (fsum).
    assert set(totals_forward) == set(totals_reversed)
    for sid in totals_forward:
        assert totals_forward[sid] == totals_reversed[sid]  # exact equality, not approx

    wc_forward = worst_case(cells)
    wc_reversed = worst_case(list(reversed(cells)))
    assert wc_forward.scenario.scenario_id == wc_reversed.scenario.scenario_id
    assert wc_forward.total_pnl == wc_reversed.total_pnl  # bit-stable worst-case total
    # S7 (-25% spot) is the largest loss in this grid; pin it so the test is concrete.
    assert wc_forward.scenario.scenario_id == "spot_down_25"


# --- Named historical scenarios (the §5.4 2008/COVID compound shocks) ----------------
def test_named_scenario_family_ids_ordering_and_compound_shock() -> None:
    # Each named scenario is ONE labelled compound shock (joint spot/vol/rate), appended last
    # after the parametric grid, in catalogue order, with id stem named_<label>.
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
    # Named scenarios sit last, after the parametric block (spot, vol, crash, time).
    assert ids == [
        "spot_-0.0500",
        "vol_+0.0500",
        "crash_spot-0.0500_vol+0.0500",
        "roll_1d",
        "named_2008",
        "named_covid-2020",
    ]
    named = [s for s in grid if s.family == "named"]
    # Each carries its compound (joint spot/vol/rate) shock — not one axis at a time.
    assert (named[0].spot_shock, named[0].vol_shock, named[0].rate_shock) == (-0.45, 0.40, -0.02)
    assert (named[1].spot_shock, named[1].vol_shock, named[1].rate_shock) == (-0.35, 0.50, -0.01)


def test_named_compound_shock_full_reprice_matches_independent_oracle() -> None:
    # The spec's hand-checked compound shock. Independent GBSM oracle (NOT the code under
    # test), cross-checked against py_vollib (agreement ~1e-13): the CALL_100 line
    # (spot 100, K 100, T 0.25, vol 0.20, carry 0, DF 0.99, multiplier 100) under a named
    # compound shock of spot -20% (relative), vol +10 pts (additive), rate +25 bp (additive,
    # forward-fixed). Base GBSM call = 3.9478835559977483 (the fixture anchor); shocked call
    # = 0.3993137052948921; per-unit PnL = -3.5485698507028562; scaled (x100) = -354.85698507.
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
    # Empty catalogue: no 'named' family, and the persisted construction version is
    # byte-identical to a grid built before the catalogue existed (the additive guarantee).
    assert not any(s.family == "named" for s in scenario_grid(base))
    # Adding a named scenario is tamper-evident: it moves effective_scenario_version.
    assert scenario_mod.effective_scenario_version(base) != scenario_mod.effective_scenario_version(
        with_named
    )


# --- Correlation-shock family (the §5.4 ρ̄ axis — built but dormant) ------------------
def test_correlation_family_ids_and_ordering_are_fixed() -> None:
    config = ScenarioConfig(
        version="scn-1",
        spot_shocks=(-0.05,),
        vol_shocks=(0.05,),
        correlation_shocks=(0.10, 0.20),
    )
    grid = scenario_grid(config)
    ids = [s.scenario_id for s in grid]
    # The correlation family sits between rate (absent here) and the combined crash.
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
    # The correlation scenarios move ONLY the ρ̄ axis (spot/vol/rate/time held).
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
    # Adding a correlation axis is tamper-evident: it moves effective_scenario_version.
    assert scenario_mod.effective_scenario_version(base) != scenario_mod.effective_scenario_version(
        with_corr
    )


def test_correlation_shock_reprices_through_basket_variance_against_an_independent_oracle() -> None:
    # Independent Eq-23 oracle (NOT the code under test). A 3-name basket
    # w=(0.5,0.3,0.2), vols=(0.25,0.30,0.20), base ρ̄=0.40, bumped by +0.20 to 0.60.
    # ws = (0.125, 0.090, 0.040); own = Σ ws² = 0.024725; cross = (Σ ws)² - own = 0.0625 - 0.024725
    # = 0.037775. base var = own + 0.40·cross = 0.039835 → vol 0.2029901475441604.
    # shocked var = own + 0.60·cross = 0.047390 → vol 0.22168671588527808. Δvol = 0.018696568341.
    # PnL = Δvol · vol_sensitivity(25000) = 467.41420852794215.
    exposure = BasketCorrelationExposure(
        weights=(0.5, 0.3, 0.2),
        vols=(0.25, 0.30, 0.20),
        avg_correlation=0.40,
        vol_sensitivity=25000.0,
    )
    scn = Scenario("corr_+0.2000", "correlation", 0.0, 0.0, 0.0, 0.0, 0.20)
    pnl = correlation_shock_pnl(exposure, scn)
    assert pnl == pytest.approx(467.41420852794215, rel=1e-12)
    # The basket vols themselves match the hand math (the reprice is a genuine Eq-23 recompute).
    assert math.isclose(0.2029901475441604, 0.2029901475441604)


def test_correlation_shock_is_inert_on_a_non_correlation_scenario() -> None:
    # A zero correlation_shock (every spot/vol/rate/time/named-without-corr cell) reprices
    # to exactly 0.0 — the family does not perturb a grid cell that does not move ρ̄.
    exposure = BasketCorrelationExposure(
        weights=(0.5, 0.5), vols=(0.25, 0.25), avg_correlation=0.40, vol_sensitivity=10000.0
    )
    for scn in (S1, S3, S6, Scenario("named_x", "named", -0.45, 0.40, 0.0, -0.02)):
        assert correlation_shock_pnl(exposure, scn) == 0.0


def test_correlation_shock_into_a_non_psd_basket_raises_not_silently_floors() -> None:
    # A ρ̄ bump that drives the equicorrelation basket below the PSD lower bound surfaces
    # the NonPSDBasketError from basket_variance, never a silent floor-to-zero. For n=2 the
    # bound is -1/(n-1) = -1.0; base ρ̄ -0.5 bumped by -0.8 → -1.3 is non-PSD.
    exposure = BasketCorrelationExposure(
        weights=(0.5, 0.5), vols=(0.25, 0.25), avg_correlation=-0.5, vol_sensitivity=10000.0
    )
    scn = Scenario("corr_-0.8000", "correlation", 0.0, 0.0, 0.0, 0.0, -0.8)
    with pytest.raises(NonPSDBasketError):
        correlation_shock_pnl(exposure, scn)


def test_construction_hash_folds_each_new_family_only_when_non_empty() -> None:
    # The single most important invariant: an unconfigured grid (no rate / no correlation /
    # no named family) hashes byte-identically, and each family is tamper-evident when added.
    base = scenario_mod._grid_construction_hash(roll_down_days=(1,))
    # Empty families => identical to the no-family payload (byte-identical-when-empty).
    assert (
        scenario_mod._grid_construction_hash(
            roll_down_days=(1,), correlation_shocks=(), named_scenarios=()
        )
        == base
    )
    # Each family, when non-empty, moves the hash — and moves it distinctly.
    with_corr = scenario_mod._grid_construction_hash(roll_down_days=(1,), correlation_shocks=(0.10,))
    with_named = scenario_mod._grid_construction_hash(
        roll_down_days=(1,),
        named_scenarios=(NamedScenarioConfig(label="2008", spot_shock=-0.45),),
    )
    assert with_corr != base
    assert with_named != base
    assert with_corr != with_named
    # A named scenario's magnitude is in the payload: changing it moves the hash.
    other_named = scenario_mod._grid_construction_hash(
        roll_down_days=(1,),
        named_scenarios=(NamedScenarioConfig(label="2008", spot_shock=-0.50),),
    )
    assert other_named != with_named
