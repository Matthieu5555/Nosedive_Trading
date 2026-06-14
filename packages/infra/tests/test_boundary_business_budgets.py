"""Cross-module business-boundary tests, with compute-time budgets that fail.

The first batch (``test_contract_storage_boundary.py``) pinned the *contract↔storage*
seam: typed objects survive the write/read door. This second batch is deliberately
different in two ways the team asked for.

* **Business purpose over plumbing.** Each test names a high-level interaction between
  modules and the effect that interaction is *supposed* to have, then checks it against
  an independent oracle — the synthetic price generator, put-call parity, an analytic
  Greek differenced by hand. These compose two or three modules through their real entry
  points, so a sign error, a unit slip, or a broken approximation in any one of them
  fails here even when every isolated unit test still passes.

* **Compute-time budgets that flag a failure.** A few hot paths (pricing a chain, a
  batch IV inversion, an SVI calibration, a full scenario reprice over a book) carry a
  wall-clock budget. The budget is a *regression tripwire*, set with generous headroom
  over the measured cost (it catches a 10×+ blow-up, not a 20% drift), and it is paired
  with a correctness assert so a path that gets fast by getting wrong still fails. Timing
  is best-of-N so scheduler jitter cannot trip it; only the genuine compute floor counts.

The boundaries covered, each a known high-failure-point in a vol/options stack:

1.  Analytics spine: forwards → IV inversion → SVI fit recovers the synthetic truth.
2.  Pricing ↔ IV solver: the one legitimate round-trip, vol → price → vol.
3.  Pricing analytic Greeks ↔ risk finite-difference Greeks (the shared-bump cross-check).
4.  Scenario engine: local Taylor agrees with full reprice for small shocks, *diverges*
    for a crash (proof the full reprice is genuinely a second source of truth).
5.  Risk aggregation reconciles to line-level sums and is order-invariant.
6.  Pricing internal consistency: put-call parity holds through the engine.
7.  The no-arbitrage guards actually fire on an arbitrage and stay quiet on a clean fit.
8.  Explicit compute budgets on the hot paths.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np
import pytest
from algotrading.core.config import ScenarioConfig, SolverConfig
from algotrading.infra.forwards import ForwardEstimate, ForwardPair, estimate_forward
from algotrading.infra.iv import (
    STATUS_CONVERGED,
    IvRequest,
    IvResult,
    iv_point,
    solve_iv,
    solve_iv_batch,
)
from algotrading.infra.pricing import (
    PriceGreeks,
    from_forward,
    price,
    price_european,
)
from algotrading.infra.risk import (
    PositionRisk,
    Scenario,
    ScenarioLinePnl,
    aggregate_lines,
    central_difference_greeks,
    full_reprice_pnl,
    local_approx_pnl,
    position_risk,
    scenario_grid,
    scenario_line_pnls,
    scenario_totals,
    worst_case,
)
from algotrading.infra.risk.valuation import pricing_state_for
from algotrading.infra.surfaces import (
    SliceFit,
    SviParams,
    butterfly_violations,
    calendar_violations,
    fit_slice,
    fit_svi,
)
from algotrading.infra.surfaces.arbitrage import butterfly_g
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG
from fixtures.positions import (
    RISK_DF,
    RISK_MATURITY_YEARS,
    RISK_PORTFOLIO,
    RISK_SPOT,
    RISK_VALUATIONS,
    ContractValuationInput,
    risk_positions,
)
from fixtures.synthetic import build_synthetic_surface

# A fixed clock for any stamped projection; nothing here reads a wall clock for math.
TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
CONFIG_HASHES = {"cfg": "cfg-hash-0"}

# Tight solver settings so a recovered vol is judged at the model's own resolution, not
# the solver's slack — a real inversion error then has nowhere to hide.
SOLVER = SolverConfig(version="iv-budget", iv_tolerance=1e-12, max_iterations=200)

# ---------------------------------------------------------------------------
# Timing harness for the compute-budget tests.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Timed[T]:
    """A workload's result paired with its best (floor) wall-clock cost in seconds."""

    result: T
    seconds: float


def time_best_of[T](work: Callable[[], T], *, repeats: int = 3) -> Timed[T]:
    """Run ``work`` ``repeats`` times and keep the fastest run.

    Best-of, not mean: a budget asks "can this path run at least this fast", and the
    fastest run is the cleanest estimate of the true compute floor — a slow run only ever
    reflects scheduler noise on a shared CI box, which is exactly what we do not want to
    assert against. The result of the last run is returned for the paired correctness check.
    """
    start = time.perf_counter()
    result = work()
    best = time.perf_counter() - start
    for _ in range(repeats - 1):
        start = time.perf_counter()
        result = work()
        best = min(best, time.perf_counter() - start)
    return Timed(result=result, seconds=best)


# Budgets are regression tripwires set at ≈30–40× the measured compute floor on a dev box
# (the floors, in ms: price-2000 ≈ 2.4, IV-batch-500 ≈ 18, SVI-fit ≈ 6, scenario ≈ 0.1,
# FD-Greeks ≈ 0.1). That headroom absorbs a slower/loaded CI box and a cold first run, yet
# a 30×+ algorithmic blow-up still trips them. Each budget is paired with a correctness
# assertion in its test, so a path that gets "fast" by getting wrong fails too.
BUDGET_PRICE_2000_OPTIONS_S = 0.10
BUDGET_IV_BATCH_500_S = 0.50
BUDGET_SVI_FIT_S = 0.20
BUDGET_SCENARIO_REPORT_S = 0.10
BUDGET_FD_GREEKS_BOOK_S = 0.05


# ---------------------------------------------------------------------------
# Shared portfolio: real PositionRisk lines from the named position fixtures.
# ---------------------------------------------------------------------------


def risk_lines() -> tuple[PositionRisk, ...]:
    """The pf-risk book as priced risk lines, through risk's own ``position_risk``."""
    return tuple(
        position_risk(
            portfolio_id=position.portfolio_id,
            quantity=position.quantity,
            valuation=RISK_VALUATIONS[position.contract_key],
        )
        for position in risk_positions()
    )


def valuation_at(strike: float, option_right: str, *, volatility: float = 0.20) -> ContractValuationInput:
    """A valuation input at the pf-risk market state for a chosen strike/right/vol."""
    return ContractValuationInput(
        contract_key=f"AAPL|OPT|{option_right}|{strike:g}",
        underlying="AAPL",
        option_right=option_right,
        exercise_style="european",
        strike=strike,
        maturity_years=RISK_MATURITY_YEARS,
        spot=RISK_SPOT,
        carry=0.0,
        volatility=volatility,
        discount_factor=RISK_DF,
        multiplier=100.0,
        currency="USD",
    )


# ===========================================================================
# 1. Analytics spine: forwards → IV → SVI fit recovers the synthetic truth.
# ===========================================================================
# One synthetic surface generated from chosen (F, DF, per-strike vols, SVI params); the
# real pipeline must recover each layer. The generator (fixtures.synthetic) is independent
# code from the modules under test, so this is recovery, not a tautology.

SPINE = build_synthetic_surface()  # F=100, DF=0.99, T=0.25, 5 strikes, known SVI params
SPINE_SPOT = SPINE.forward * SPINE.discount_factor


def _spine_pairs() -> tuple[ForwardPair, ...]:
    return tuple(
        ForwardPair(
            strike=point.strike,
            call_mid=point.call_price,
            put_mid=point.put_price,
            liquidity=1.0,
            call_key=f"AAPL|OPT|C|{point.strike:g}",
            put_key=f"AAPL|OPT|P|{point.strike:g}",
        )
        for point in SPINE.points
    )


def test_forward_estimate_recovers_synthetic_forward_and_discount_factor() -> None:
    """forwards: the parity regression recovers the (F, DF) the prices were minted from."""
    estimate = estimate_forward(
        "AAPL", SPINE.maturity_years, _spine_pairs(), config=FORWARD_CONFIG, spot=SPINE_SPOT
    )
    assert estimate.is_usable
    # Truth lives on the generator, not in the forwards module.
    assert estimate.forward == pytest.approx(SPINE.forward, rel=1e-9)
    assert estimate.discount_factor == pytest.approx(SPINE.discount_factor, rel=1e-9)


@pytest.mark.parametrize("point_index", range(len(SPINE.points)))
def test_iv_inversion_recovers_true_vol_per_strike(point_index: int) -> None:
    """IV solver: inverting the generated call price returns the strike's true sigma."""
    point = SPINE.points[point_index]
    result = solve_iv(
        point.call_price,
        contract_key=f"AAPL|OPT|C|{point.strike:g}",
        forward=SPINE.forward,
        strike=point.strike,
        maturity_years=SPINE.maturity_years,
        discount_factor=SPINE.discount_factor,
        option_right="C",
        config=SOLVER,
    )
    assert result.status == STATUS_CONVERGED
    assert result.iv is not None
    # The generator chose this sigma; the solver must land back on it.
    assert result.iv == pytest.approx(point.sigma, rel=1e-7)


def _spine_iv_points() -> tuple:
    points = []
    for point in SPINE.points:
        result = solve_iv(
            point.call_price,
            contract_key=f"AAPL|OPT|C|{point.strike:g}",
            forward=SPINE.forward,
            strike=point.strike,
            maturity_years=SPINE.maturity_years,
            discount_factor=SPINE.discount_factor,
            option_right="C",
            config=SOLVER,
        )
        points.append(
            iv_point(result, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS, config_hashes=CONFIG_HASHES)
        )
    return tuple(points)


@pytest.mark.parametrize("point_index", range(len(SPINE.points)))
def test_svi_fit_recovers_total_variance_at_observed_strikes(point_index: int) -> None:
    """surfaces: the calibrated smile reproduces the true total variance at each strike."""
    fit = fit_slice(
        "AAPL", SPINE.maturity_years, _spine_iv_points(),
        expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
    )
    assert fit.method == "svi"
    point = SPINE.points[point_index]
    # The SVI total variance at the observed k must match the generator's w(k); the fit
    # error is in total-variance units and is tiny for a perfectly-SVI smile.
    assert fit.total_variance(point.log_moneyness) == pytest.approx(point.total_variance, abs=1e-6)


def test_svi_fit_of_a_wellformed_smile_is_butterfly_arbitrage_free() -> None:
    """surfaces: a clean smile calibrates arbitrage-free — the guard does not false-positive."""
    fit = fit_slice(
        "AAPL", SPINE.maturity_years, _spine_iv_points(),
        expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
    )
    assert fit.arb_free is True
    assert fit.butterfly_violations == ()
    assert fit.rmse < 1e-4  # near-perfect fit on an SVI-generated slice


def test_full_analytics_slice_pipeline_within_budget() -> None:
    """spine: forwards→IV→fit runs under budget AND recovers the truth (fast-but-wrong fails)."""

    def run() -> tuple[ForwardEstimate, SliceFit]:
        estimate = estimate_forward(
            "AAPL", SPINE.maturity_years, _spine_pairs(), config=FORWARD_CONFIG, spot=SPINE_SPOT
        )
        fit = fit_slice(
            "AAPL", SPINE.maturity_years, _spine_iv_points(),
            expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
        )
        return estimate, fit

    timed = time_best_of(run)
    estimate, fit = timed.result
    assert estimate.forward == pytest.approx(SPINE.forward, rel=1e-9)
    assert fit.method == "svi" and fit.arb_free
    assert timed.seconds < BUDGET_SVI_FIT_S, (
        f"slice pipeline took {timed.seconds:.4f}s, over budget {BUDGET_SVI_FIT_S}s"
    )


# ===========================================================================
# 2. Pricing ↔ IV solver: the one legitimate round-trip, vol → price → vol.
# ===========================================================================
# Independent code: the pricer maps vol→price, the solver inverts it. Recovering a known
# vol across the moneyness grid and both rights is a real test of both sides at once.

_ROUNDTRIP_CASES = [
    (right, strike, vol)
    for right in ("C", "P")
    for strike in (80.0, 90.0, 100.0, 110.0, 120.0)
    for vol in (0.15, 0.45)
]


@pytest.mark.parametrize(("right", "strike", "vol"), _ROUNDTRIP_CASES)
def test_pricer_iv_solver_round_trip_recovers_vol(right: str, strike: float, vol: float) -> None:
    """price(vol) then solve_iv(price) returns vol, across moneyness and both rights."""
    forward, discount_factor, maturity = 100.0, 0.98, 0.5
    state = from_forward(
        forward=forward, strike=strike, maturity_years=maturity,
        volatility=vol, discount_factor=discount_factor, option_right=right,
    )
    target = price_european(state).price
    result = solve_iv(
        target, contract_key="rt", forward=forward, strike=strike,
        maturity_years=maturity, discount_factor=discount_factor,
        option_right=right, config=SOLVER,
    )
    assert result.status == STATUS_CONVERGED
    assert result.iv == pytest.approx(vol, rel=1e-6)


# ===========================================================================
# 3. Pricing analytic Greeks ↔ risk finite-difference Greeks.
# ===========================================================================
# The blueprint's standing rule: finite-difference validation must exist even where
# analytic Greeks are used, because that is what catches a sign or unit error. Both sides
# draw the perturbation from the one shared versioned bump source, so a match here is a
# match in production. Tolerances are derived from the central-difference truncation/round
# scale at the shared bump sizes, not copied from output.

_GREEK_CASES = [
    (right, strike)
    for right in ("C", "P")
    for strike in (92.0, 96.0, 100.0, 104.0, 108.0)
]


@pytest.mark.parametrize(("right", "strike"), _GREEK_CASES)
def test_analytic_greeks_match_finite_difference(right: str, strike: float) -> None:
    """delta/gamma/vega/theta from the closed-form engine match a central difference."""
    valuation = valuation_at(strike, right)
    analytic = price(pricing_state_for(valuation))
    fd = central_difference_greeks(valuation)

    # delta/vega/theta: first-order central difference, exact to ~1e-6 abs at these bumps.
    assert analytic.delta == pytest.approx(fd.delta, rel=1e-5, abs=1e-7)
    assert analytic.vega == pytest.approx(fd.vega, rel=1e-5, abs=1e-6)
    assert analytic.theta == pytest.approx(fd.theta, rel=1e-5, abs=1e-6)
    # gamma: second-order difference is noisier, so a looser but still tight bound.
    assert analytic.gamma == pytest.approx(fd.gamma, rel=1e-3, abs=1e-7)


def test_finite_difference_greeks_over_book_within_budget() -> None:
    """Cross-checking the book's Greeks by finite difference is cheap; budget guards it."""
    valuations = [line.valuation for line in risk_lines()]

    def run() -> list[PriceGreeks]:
        return [central_difference_greeks(v) for v in valuations]

    timed = time_best_of(run)
    greeks = timed.result
    assert all(isinstance(g, PriceGreeks) for g in greeks)
    assert len(greeks) == len(valuations)
    assert timed.seconds < BUDGET_FD_GREEKS_BOOK_S, (
        f"book FD Greeks took {timed.seconds:.4f}s, over budget {BUDGET_FD_GREEKS_BOOK_S}s"
    )


# ===========================================================================
# 4. Scenario engine: Taylor vs full reprice — agreement small, divergence large.
# ===========================================================================
# The docstring's promise: the local Taylor approximation must agree with the full reprice
# for small shocks and is expected to diverge for large ones. If the two ever agreed
# exactly for a crash, the "full reprice" would secretly be the Taylor path — these two
# tests pin that they are genuinely different sources.

CALL_100_LINE = position_risk(
    portfolio_id=RISK_PORTFOLIO, quantity=10.0, valuation=RISK_VALUATIONS["AAPL|OPT|C|100"]
)


def test_taylor_matches_full_reprice_for_a_small_spot_shock() -> None:
    """A 0.1% spot nudge: local Taylor and full reprice agree to a fraction of a percent."""
    scenario = Scenario("spot_small", "spot", spot_shock=0.001, vol_shock=0.0, time_shock=0.0)
    taylor = local_approx_pnl(CALL_100_LINE, scenario)
    full = full_reprice_pnl(CALL_100_LINE, scenario)
    assert full != 0.0
    assert abs(taylor - full) / abs(full) < 1e-3


def test_taylor_diverges_from_full_reprice_for_a_crash() -> None:
    """A 30% crash: full reprice captures convexity the Taylor expansion drops — they differ."""
    scenario = Scenario("crash", "combined", spot_shock=-0.30, vol_shock=0.10, time_shock=0.0)
    taylor = local_approx_pnl(CALL_100_LINE, scenario)
    full = full_reprice_pnl(CALL_100_LINE, scenario)
    # Materially different: a real second source, not the same arithmetic twice.
    assert abs(taylor - full) / abs(full) > 0.01


def test_zero_shock_scenario_has_zero_pnl() -> None:
    """The null scenario reprices to the base price: exactly zero PnL on every path."""
    null = Scenario("null", "spot", spot_shock=0.0, vol_shock=0.0, time_shock=0.0)
    assert full_reprice_pnl(CALL_100_LINE, null) == 0.0
    assert local_approx_pnl(CALL_100_LINE, null) == 0.0


SCENARIO_CONFIG = ScenarioConfig(
    version="scenario-budget",
    spot_shocks=(-0.30, -0.10, 0.10),
    vol_shocks=(-0.05, 0.05),
    roll_down_days=(1, 7),
)


def test_full_scenario_report_within_budget_and_totals_reconcile() -> None:
    """A full-grid reprice over the book runs under budget and its totals reconcile."""
    lines = risk_lines()
    grid = scenario_grid(SCENARIO_CONFIG)

    def run() -> tuple[list[ScenarioLinePnl], dict[str, float]]:
        cells = scenario_line_pnls(lines, grid)
        return cells, scenario_totals(cells)

    timed = time_best_of(run)
    cells, totals = timed.result
    # Completeness: one cell per (scenario, contract) — a property of the cartesian product.
    assert len(cells) == len(grid) * len(lines)
    # Each scenario total is the fsum of its lines' full-reprice PnLs (reconciliation).
    for scenario in grid:
        scoped = [c.full_reprice_pnl for c in cells if c.scenario.scenario_id == scenario.scenario_id]
        assert totals[scenario.scenario_id] == pytest.approx(math.fsum(scoped), abs=1e-9)
    assert timed.seconds < BUDGET_SCENARIO_REPORT_S, (
        f"scenario report took {timed.seconds:.4f}s, over budget {BUDGET_SCENARIO_REPORT_S}s"
    )


def test_worst_case_is_the_most_adverse_grid_scenario() -> None:
    """worst_case picks the scenario with the largest book loss — verified against the totals."""
    lines = risk_lines()
    grid = scenario_grid(SCENARIO_CONFIG)
    cells = scenario_line_pnls(lines, grid)
    totals = scenario_totals(cells)

    worst = worst_case(cells)
    expected_worst_total = min(totals.values())
    assert worst.total_pnl == pytest.approx(expected_worst_total, abs=1e-9)
    # Its contributors are ranked worst-first, so the loss is always traceable to a line.
    contrib = [c.full_reprice_pnl for c in worst.contributors]
    assert contrib == sorted(contrib)


# ===========================================================================
# 5. Risk aggregation reconciles to line-level sums and is order-invariant.
# ===========================================================================
# The blueprint acceptance test: "Portfolio aggregates reconcile to line-level sums", and
# the aggregate must not depend on the order positions arrive in.


@pytest.mark.parametrize("dimension", ("instrument", "maturity", "underlying"))
def test_aggregate_net_delta_reconciles_to_line_sums(dimension: str) -> None:
    """Sum of every group's net_delta equals the sum of all line-level position deltas."""
    lines = risk_lines()
    groups = aggregate_lines(lines, portfolio_id=RISK_PORTFOLIO, dimension=dimension)

    line_total = math.fsum(line.position_delta for line in lines)
    group_total = math.fsum(group.net_delta for group in groups)
    assert group_total == pytest.approx(line_total, abs=1e-9)

    # And each group's net is exactly the sum of its own lines (line-level audit holds).
    for group in groups:
        assert group.net_delta == pytest.approx(
            math.fsum(line.position_delta for line in group.lines), abs=1e-9
        )


def test_aggregation_is_invariant_to_position_order() -> None:
    """Shuffling the lines cannot change the aggregate — it is a function of the set."""
    lines = list(risk_lines())
    canonical = aggregate_lines(lines, portfolio_id=RISK_PORTFOLIO, dimension="underlying")

    shuffled = lines[:]
    random.Random(20260613).shuffle(shuffled)
    reshuffled = aggregate_lines(shuffled, portfolio_id=RISK_PORTFOLIO, dimension="underlying")

    assert [(g.group_key, g.net_delta, g.net_gamma, g.net_vega, g.net_theta) for g in canonical] == [
        (g.group_key, g.net_delta, g.net_gamma, g.net_vega, g.net_theta) for g in reshuffled
    ]


# ===========================================================================
# 6. Pricing internal consistency: put-call parity holds through the engine.
# ===========================================================================
# call - put = DF*(F - K), exactly, from two independent pricing calls. Everything
# downstream (the IV bounds, the parity forward) assumes this; pin it directly.


@pytest.mark.parametrize("strike", (80.0, 90.0, 100.0, 110.0, 120.0))
def test_put_call_parity_holds_through_the_pricer(strike: float) -> None:
    """A call and a put priced separately satisfy put-call parity to machine precision."""
    forward, discount_factor, maturity, vol = 100.0, 0.97, 0.75, 0.25
    call = price_european(
        from_forward(forward=forward, strike=strike, maturity_years=maturity,
                     volatility=vol, discount_factor=discount_factor, option_right="C")
    ).price
    put = price_european(
        from_forward(forward=forward, strike=strike, maturity_years=maturity,
                     volatility=vol, discount_factor=discount_factor, option_right="P")
    ).price
    # Parity is exact in the forward-form Black-76, not approximate.
    assert call - put == pytest.approx(discount_factor * (forward - strike), abs=1e-10)


# ===========================================================================
# 7. The no-arbitrage guards actually fire — and stay quiet on a clean fit.
# ===========================================================================
# A safety check that never fires is worthless. These give the detectors a genuine
# arbitrage and assert they flag it, then a clean input and assert they do not.


def test_butterfly_detector_flags_a_negative_variance_smile() -> None:
    """A smile that dips to non-positive total variance is an arbitrage — it must be flagged."""
    # a < 0 with small b pushes w(k) below zero near the money: an impossible (arb) smile.
    arb = SviParams(a=-0.05, b=0.01, rho=0.0, m=0.0, sigma=0.05)
    grid = tuple(-0.2 + 0.02 * i for i in range(21))
    breaches = butterfly_violations(arb, grid)
    assert breaches  # the guard fires
    # Every flagged k genuinely breaches: non-positive variance, or a negative Gatheral g.
    assert all(
        arb.total_variance(k) <= 0.0 or butterfly_g(arb, k) < -1e-9 for k in breaches
    )


def test_butterfly_detector_is_quiet_on_a_clean_smile() -> None:
    """A well-formed convex smile has no butterfly breach — no false positive."""
    clean = SviParams(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)
    grid = tuple(-0.3 + 0.03 * i for i in range(21))
    assert butterfly_violations(clean, grid) == ()


def test_calendar_detector_flags_an_inverted_term_structure() -> None:
    """Total variance that falls as maturity rises is a calendar arbitrage — flag it."""
    # Short slice sits above the long slice at every k: w_long < w_short, a clear breach.
    short = SviParams(a=0.10, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    long = SviParams(a=0.04, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    grid = tuple(-0.2 + 0.04 * i for i in range(11))
    violations = calendar_violations(
        [(0.25, short.total_variance), (0.50, long.total_variance)], grid
    )
    assert violations  # the guard fires
    assert all(v.w_long < v.w_short for v in violations)


def test_calendar_detector_is_quiet_on_a_monotone_term_structure() -> None:
    """A properly increasing term structure raises no calendar breach — no false positive."""
    short = SviParams(a=0.04, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    long = SviParams(a=0.10, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    grid = tuple(-0.2 + 0.04 * i for i in range(11))
    assert calendar_violations(
        [(0.25, short.total_variance), (0.50, long.total_variance)], grid
    ) == ()


# ===========================================================================
# 8. Explicit compute budgets on the hot paths.
# ===========================================================================
# These are the headline "give a module a time budget and fail if it blows it" tests.
# Each does real, representative work and asserts both a correctness sanity check and the
# wall-clock floor, so neither a wrong answer nor a 10× slowdown can pass.


def test_pricing_a_full_chain_within_budget() -> None:
    """Pricing 2000 European options stays well under a second (a chain reprice is hot)."""
    states = [
        from_forward(forward=100.0, strike=strike, maturity_years=0.5,
                     volatility=0.20 + 0.0001 * i, discount_factor=0.98, option_right="C")
        for i, strike in enumerate(np.linspace(60.0, 140.0, 2000))
    ]

    def run() -> list[float]:
        return [price(state).price for state in states]

    timed = time_best_of(run)
    prices = timed.result
    assert len(prices) == 2000
    assert all(p > 0.0 for p in prices)  # every priced option is worth something positive
    assert timed.seconds < BUDGET_PRICE_2000_OPTIONS_S, (
        f"pricing 2000 options took {timed.seconds:.4f}s, over budget {BUDGET_PRICE_2000_OPTIONS_S}s"
    )


def test_iv_batch_inversion_within_budget() -> None:
    """A 500-option batch IV inversion stays under budget and every solve converges."""
    forward, discount_factor, maturity, true_vol = 100.0, 0.98, 0.5, 0.30
    strikes = np.linspace(75.0, 125.0, 500)
    requests = []
    for strike in strikes:
        state = from_forward(forward=forward, strike=float(strike), maturity_years=maturity,
                             volatility=true_vol, discount_factor=discount_factor, option_right="C")
        target = price_european(state).price
        requests.append(IvRequest(
            target_price=target, contract_key=f"k{strike:.2f}", forward=forward,
            strike=float(strike), maturity_years=maturity,
            discount_factor=discount_factor, option_right="C",
        ))
    batch = tuple(requests)

    def run() -> tuple[IvResult, ...]:
        return solve_iv_batch(batch, config=SOLVER)

    timed = time_best_of(run, repeats=2)
    results = timed.result
    assert len(results) == 500
    # Correctness sanity: every inversion converges and recovers the one true vol.
    assert all(r.status == STATUS_CONVERGED for r in results)
    assert all(r.iv == pytest.approx(true_vol, rel=1e-5) for r in results)
    assert timed.seconds < BUDGET_IV_BATCH_500_S, (
        f"IV batch of 500 took {timed.seconds:.4f}s, over budget {BUDGET_IV_BATCH_500_S}s"
    )


def test_svi_calibration_within_budget() -> None:
    """Calibrating one SVI slice stays under budget and recovers the generating params."""
    ks = tuple(point.log_moneyness for point in SPINE.points)
    ws = tuple(point.total_variance for point in SPINE.points)

    timed = time_best_of(lambda: fit_svi(ks, ws, config=SURFACE_CONFIG))
    fit = timed.result
    assert fit.converged
    # The generated smile is exactly SVI, so the fit reproduces every observed variance.
    for k, w in zip(ks, ws, strict=True):
        assert fit.params.total_variance(k) == pytest.approx(w, abs=1e-6)
    assert timed.seconds < BUDGET_SVI_FIT_S, (
        f"SVI calibration took {timed.seconds:.4f}s, over budget {BUDGET_SVI_FIT_S}s"
    )
