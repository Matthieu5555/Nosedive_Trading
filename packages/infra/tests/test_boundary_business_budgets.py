from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
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

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
CONFIG_HASHES = {"cfg": "cfg-hash-0"}

SOLVER = SolverConfig(version="iv-budget", iv_tolerance=1e-12, max_iterations=200)


@dataclass(frozen=True, slots=True)
class Timed[T]:

    result: T
    seconds: float


def time_best_of[T](work: Callable[[], T], *, repeats: int = 3) -> Timed[T]:
    start = time.perf_counter()
    result = work()
    best = time.perf_counter() - start
    for _ in range(repeats - 1):
        start = time.perf_counter()
        result = work()
        best = min(best, time.perf_counter() - start)
    return Timed(result=result, seconds=best)


BUDGET_PRICE_2000_OPTIONS_S = 0.10
BUDGET_IV_BATCH_500_S = 0.50
BUDGET_SVI_FIT_S = 0.20
BUDGET_SCENARIO_REPORT_S = 0.10
BUDGET_FD_GREEKS_BOOK_S = 0.05


def risk_lines() -> tuple[PositionRisk, ...]:
    return tuple(
        position_risk(
            portfolio_id=position.portfolio_id,
            quantity=position.quantity,
            valuation=RISK_VALUATIONS[position.contract_key],
        )
        for position in risk_positions()
    )


def valuation_at(strike: float, option_right: str, *, volatility: float = 0.20) -> ContractValuationInput:
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


SPINE = build_synthetic_surface()
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
    estimate = estimate_forward(
        "AAPL", SPINE.maturity_years, _spine_pairs(), config=FORWARD_CONFIG, spot=SPINE_SPOT
    )
    assert estimate.is_usable
    assert estimate.forward == pytest.approx(SPINE.forward, rel=1e-9)
    assert estimate.discount_factor == pytest.approx(SPINE.discount_factor, rel=1e-9)


@pytest.mark.parametrize("point_index", range(len(SPINE.points)))
def test_iv_inversion_recovers_true_vol_per_strike(point_index: int) -> None:
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
    fit = fit_slice(
        "AAPL", SPINE.maturity_years, _spine_iv_points(),
        expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
    )
    assert fit.method == "svi"
    point = SPINE.points[point_index]
    assert fit.total_variance(point.log_moneyness) == pytest.approx(point.total_variance, abs=1e-6)


def test_svi_fit_of_a_wellformed_smile_is_butterfly_arbitrage_free() -> None:
    fit = fit_slice(
        "AAPL", SPINE.maturity_years, _spine_iv_points(),
        expiry_date=EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
    )
    assert fit.arb_free is True
    assert fit.butterfly_violations == ()
    assert fit.rmse < 1e-4


def test_full_analytics_slice_pipeline_within_budget() -> None:

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


_ROUNDTRIP_CASES = [
    (right, strike, vol)
    for right in ("C", "P")
    for strike in (80.0, 90.0, 100.0, 110.0, 120.0)
    for vol in (0.15, 0.45)
]


@pytest.mark.parametrize(("right", "strike", "vol"), _ROUNDTRIP_CASES)
def test_pricer_iv_solver_round_trip_recovers_vol(right: str, strike: float, vol: float) -> None:
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


_GREEK_CASES = [
    (right, strike)
    for right in ("C", "P")
    for strike in (92.0, 96.0, 100.0, 104.0, 108.0)
]


@pytest.mark.parametrize(("right", "strike"), _GREEK_CASES)
def test_analytic_greeks_match_finite_difference(right: str, strike: float) -> None:
    valuation = valuation_at(strike, right)
    analytic = price(pricing_state_for(valuation))
    fd = central_difference_greeks(valuation)

    assert analytic.delta == pytest.approx(fd.delta, rel=1e-5, abs=1e-7)
    assert analytic.vega == pytest.approx(fd.vega, rel=1e-5, abs=1e-6)
    assert analytic.theta == pytest.approx(fd.theta, rel=1e-5, abs=1e-6)
    assert analytic.gamma == pytest.approx(fd.gamma, rel=1e-3, abs=1e-7)

    h = 1e-5
    vol = valuation.volatility
    repriced = lambda **kw: price(pricing_state_for(replace(valuation, **kw)))  # noqa: E731
    vanna_fd = (repriced(volatility=vol + h).delta - repriced(volatility=vol - h).delta) / (2 * h)
    volga_fd = (repriced(volatility=vol + h).vega - repriced(volatility=vol - h).vega) / (2 * h)
    rate = -math.log(valuation.discount_factor) / valuation.maturity_years
    at_t = lambda t: repriced(maturity_years=t, discount_factor=math.exp(-rate * t))  # noqa: E731
    t0 = valuation.maturity_years
    charm_fd = -(at_t(t0 + h).delta - at_t(t0 - h).delta) / (2 * h)
    assert analytic.vanna == pytest.approx(vanna_fd, rel=1e-4, abs=1e-8)
    assert analytic.volga == pytest.approx(volga_fd, rel=1e-4, abs=1e-8)
    assert analytic.charm == pytest.approx(charm_fd, rel=1e-4, abs=1e-8)


def test_finite_difference_greeks_over_book_within_budget() -> None:
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


CALL_100_LINE = position_risk(
    portfolio_id=RISK_PORTFOLIO, quantity=10.0, valuation=RISK_VALUATIONS["AAPL|OPT|C|100"]
)


def test_taylor_matches_full_reprice_for_a_small_spot_shock() -> None:
    scenario = Scenario("spot_small", "spot", spot_shock=0.001, vol_shock=0.0, time_shock=0.0)
    taylor = local_approx_pnl(CALL_100_LINE, scenario)
    full = full_reprice_pnl(CALL_100_LINE, scenario)
    assert full != 0.0
    assert abs(taylor - full) / abs(full) < 1e-3


def test_taylor_diverges_from_full_reprice_for_a_crash() -> None:
    scenario = Scenario("crash", "combined", spot_shock=-0.30, vol_shock=0.10, time_shock=0.0)
    taylor = local_approx_pnl(CALL_100_LINE, scenario)
    full = full_reprice_pnl(CALL_100_LINE, scenario)
    assert abs(taylor - full) / abs(full) > 0.01


def test_zero_shock_scenario_has_zero_pnl() -> None:
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
    lines = risk_lines()
    grid = scenario_grid(SCENARIO_CONFIG)

    def run() -> tuple[list[ScenarioLinePnl], dict[str, float]]:
        cells = scenario_line_pnls(lines, grid)
        return cells, scenario_totals(cells)

    timed = time_best_of(run)
    cells, totals = timed.result
    assert len(cells) == len(grid) * len(lines)
    for scenario in grid:
        scoped = [c.full_reprice_pnl for c in cells if c.scenario.scenario_id == scenario.scenario_id]
        assert totals[scenario.scenario_id] == pytest.approx(math.fsum(scoped), abs=1e-9)
    assert timed.seconds < BUDGET_SCENARIO_REPORT_S, (
        f"scenario report took {timed.seconds:.4f}s, over budget {BUDGET_SCENARIO_REPORT_S}s"
    )


def test_worst_case_is_the_most_adverse_grid_scenario() -> None:
    lines = risk_lines()
    grid = scenario_grid(SCENARIO_CONFIG)
    cells = scenario_line_pnls(lines, grid)
    totals = scenario_totals(cells)

    worst = worst_case(cells)
    expected_worst_total = min(totals.values())
    assert worst.total_pnl == pytest.approx(expected_worst_total, abs=1e-9)
    contrib = [c.full_reprice_pnl for c in worst.contributors]
    assert contrib == sorted(contrib)


@pytest.mark.parametrize("dimension", ("instrument", "maturity", "underlying"))
def test_aggregate_net_delta_reconciles_to_line_sums(dimension: str) -> None:
    lines = risk_lines()
    groups = aggregate_lines(lines, portfolio_id=RISK_PORTFOLIO, dimension=dimension)

    line_total = math.fsum(line.position_delta for line in lines)
    group_total = math.fsum(group.net_delta for group in groups)
    assert group_total == pytest.approx(line_total, abs=1e-9)

    for group in groups:
        assert group.net_delta == pytest.approx(
            math.fsum(line.position_delta for line in group.lines), abs=1e-9
        )


def test_aggregation_is_invariant_to_position_order() -> None:
    lines = list(risk_lines())
    canonical = aggregate_lines(lines, portfolio_id=RISK_PORTFOLIO, dimension="underlying")

    shuffled = lines[:]
    random.Random(20260613).shuffle(shuffled)
    reshuffled = aggregate_lines(shuffled, portfolio_id=RISK_PORTFOLIO, dimension="underlying")

    assert [(g.group_key, g.net_delta, g.net_gamma, g.net_vega, g.net_theta) for g in canonical] == [
        (g.group_key, g.net_delta, g.net_gamma, g.net_vega, g.net_theta) for g in reshuffled
    ]


@pytest.mark.parametrize("strike", (80.0, 90.0, 100.0, 110.0, 120.0))
def test_put_call_parity_holds_through_the_pricer(strike: float) -> None:
    forward, discount_factor, maturity, vol = 100.0, 0.97, 0.75, 0.25
    call = price_european(
        from_forward(forward=forward, strike=strike, maturity_years=maturity,
                     volatility=vol, discount_factor=discount_factor, option_right="C")
    ).price
    put = price_european(
        from_forward(forward=forward, strike=strike, maturity_years=maturity,
                     volatility=vol, discount_factor=discount_factor, option_right="P")
    ).price
    assert call - put == pytest.approx(discount_factor * (forward - strike), abs=1e-10)


def test_butterfly_detector_flags_a_negative_variance_smile() -> None:
    arb = SviParams(a=-0.05, b=0.01, rho=0.0, m=0.0, sigma=0.05)
    grid = tuple(-0.2 + 0.02 * i for i in range(21))
    breaches = butterfly_violations(arb, grid)
    assert breaches
    assert all(
        arb.total_variance(k) <= 0.0 or butterfly_g(arb, k) < -1e-9 for k in breaches
    )


def test_butterfly_detector_is_quiet_on_a_clean_smile() -> None:
    clean = SviParams(a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20)
    grid = tuple(-0.3 + 0.03 * i for i in range(21))
    assert butterfly_violations(clean, grid) == ()


def test_calendar_detector_flags_an_inverted_term_structure() -> None:
    short = SviParams(a=0.10, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    long = SviParams(a=0.04, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    grid = tuple(-0.2 + 0.04 * i for i in range(11))
    violations = calendar_violations(
        [(0.25, short.total_variance), (0.50, long.total_variance)], grid
    )
    assert violations
    assert all(v.w_long < v.w_short for v in violations)


def test_calendar_detector_is_quiet_on_a_monotone_term_structure() -> None:
    short = SviParams(a=0.04, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    long = SviParams(a=0.10, b=0.10, rho=0.0, m=0.0, sigma=0.20)
    grid = tuple(-0.2 + 0.04 * i for i in range(11))
    assert calendar_violations(
        [(0.25, short.total_variance), (0.50, long.total_variance)], grid
    ) == ()


def test_pricing_a_full_chain_within_budget() -> None:
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
    assert all(p > 0.0 for p in prices)
    assert timed.seconds < BUDGET_PRICE_2000_OPTIONS_S, (
        f"pricing 2000 options took {timed.seconds:.4f}s, over budget {BUDGET_PRICE_2000_OPTIONS_S}s"
    )


def test_iv_batch_inversion_within_budget() -> None:
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
    assert all(r.status == STATUS_CONVERGED for r in results)
    assert all(r.iv == pytest.approx(true_vol, rel=1e-5) for r in results)
    assert timed.seconds < BUDGET_IV_BATCH_500_S, (
        f"IV batch of 500 took {timed.seconds:.4f}s, over budget {BUDGET_IV_BATCH_500_S}s"
    )


def test_svi_calibration_within_budget() -> None:
    ks = tuple(point.log_moneyness for point in SPINE.points)
    ws = tuple(point.total_variance for point in SPINE.points)

    timed = time_best_of(lambda: fit_svi(ks, ws, config=SURFACE_CONFIG))
    fit = timed.result
    assert fit.converged
    for k, w in zip(ks, ws, strict=True):
        assert fit.params.total_variance(k) == pytest.approx(w, abs=1e-6)
    assert timed.seconds < BUDGET_SVI_FIT_S, (
        f"SVI calibration took {timed.seconds:.4f}s, over budget {BUDGET_SVI_FIT_S}s"
    )
