"""Tests for the implied-volatility solver (step 8).

Independent oracles, never the code under test:

* The solver-vs-pricer round-trip (TESTING.md's one legitimate round-trip): the
  *synthetic generator* prices a known sigma with Black-76 (different code from the
  solver), and the solver must recover that sigma. The pricing engine is the
  independent oracle for the solver.
* ``vollib.black.implied_volatility`` — Jaeckel's "let's be rational" inversion, a
  completely different algorithm and codebase, as a second independent oracle.
* By-hand bounds: a call price lies in ``[DF*max(F-K,0), DF*F]``; a put in
  ``[DF*max(K-F,0), DF*K]``.

Float comparisons use explicit tolerances sized to each oracle.
"""

from __future__ import annotations

import math

import pytest
from algotrading.core.config import SolverConfig
from algotrading.infra.contracts import IvPoint, table_for_contract, validate
from algotrading.infra.iv import (
    STATUS_ABOVE_MAX,
    STATUS_BELOW_INTRINSIC,
    STATUS_CONVERGED,
    STATUS_NON_CONVERGENCE,
    IvRequest,
    IvResult,
    european_price_bounds,
    iv_point,
    solve_implied_vol_scalar,
    solve_iv,
    solve_iv_batch,
)
from algotrading.infra.pricing import from_spot, price_american
from fixtures.synthetic import black_call, build_synthetic_surface
from vollib.black.implied_volatility import implied_volatility as vollib_iv

# A solver config with a tight tolerance and a generous iteration budget.
CFG = SolverConfig(version="iv-test", iv_tolerance=1e-12, max_iterations=200)

_SURFACE = build_synthetic_surface()  # F=100, DF=0.99, T=0.25
_F = _SURFACE.forward
_DF = _SURFACE.discount_factor
_T = _SURFACE.maturity_years
_RATE = -math.log(_DF) / _T  # continuously compounded r implied by DF


def _solve_call(target_price: float, strike: float) -> IvResult:
    return solve_iv(
        target_price, contract_key=f"K{strike:g}", forward=_F, strike=strike,
        maturity_years=_T, discount_factor=_DF, option_right="C", config=CFG,
    )


# --------------------------------------------------------------------------- #
# Recovery — the solver-vs-pricer oracle, plus a second independent oracle      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("point_index", range(5))
def test_recovers_known_sigma_per_strike(point_index: int) -> None:
    point = _SURFACE.points[point_index]
    result = _solve_call(point.call_price, point.strike)
    assert result.status == STATUS_CONVERGED
    # Oracle 1: the sigma the synthetic generator used to price this strike.
    assert result.iv == pytest.approx(point.sigma, abs=1e-7)
    assert result.residual < 1e-9


@pytest.mark.parametrize("point_index", range(5))
def test_matches_independent_vollib_inversion(point_index: int) -> None:
    point = _SURFACE.points[point_index]
    result = _solve_call(point.call_price, point.strike)
    # Oracle 2: vollib's let's-be-rational inversion (a different algorithm). vollib
    # takes the discounted price, the forward, and the rate implied by the DF.
    oracle = vollib_iv(point.call_price, _F, point.strike, _RATE, _T, "c")
    assert result.iv == pytest.approx(oracle, abs=1e-6)


def test_log_moneyness_and_total_variance() -> None:
    # Eq 6: k = ln(K/F). Eq 7: w = sigma^2 * T. Checked against by-hand values.
    point = _SURFACE.points[0]  # strike 80
    result = _solve_call(point.call_price, point.strike)
    assert result.iv is not None
    assert result.k == pytest.approx(math.log(point.strike / _F), rel=1e-12)
    assert result.total_variance == pytest.approx(result.iv * result.iv * _T, rel=1e-12)
    # And against the generator's own total variance for this strike.
    assert result.total_variance == pytest.approx(point.total_variance, abs=1e-7)


# --------------------------------------------------------------------------- #
# Monotonicity and stability                                                  #
# --------------------------------------------------------------------------- #
def test_higher_price_implies_higher_iv() -> None:
    # Price strictly increases in vol, so its inverse is increasing: a richer ATM
    # call must imply a higher vol.
    strike = 100.0
    low = _solve_call(3.0, strike)
    high = _solve_call(5.0, strike)
    assert low.iv is not None and high.iv is not None
    assert high.iv > low.iv


def test_small_price_perturbation_gives_bounded_iv_change() -> None:
    # A small price bump changes IV by about dPrice / vega. For this ATM-ish call vega
    # is ~ 19 per unit vol, so a 0.01 price bump moves IV by < 1e-3. Bound, not prose.
    point = _SURFACE.points[2]  # strike 100
    base = _solve_call(point.call_price, point.strike)
    bumped = _solve_call(point.call_price + 0.01, point.strike)
    assert base.iv is not None and bumped.iv is not None
    assert abs(bumped.iv - base.iv) < 1e-3
    assert bumped.iv > base.iv  # and in the right direction


# --------------------------------------------------------------------------- #
# Labeled failures — a structured diagnostic, never a bare NaN                 #
# --------------------------------------------------------------------------- #
def test_price_below_intrinsic_is_labeled() -> None:
    # A deep ITM call (strike 80) priced below its discounted intrinsic is impossible.
    intrinsic, _ = european_price_bounds(_F, 80.0, _DF, "C")
    result = _solve_call(intrinsic - 1.0, 80.0)
    assert result.iv is None
    assert result.status == STATUS_BELOW_INTRINSIC
    assert not result.converged
    assert result.residual > 0.0  # the diagnostic quantifies how far below


def test_price_above_max_is_labeled() -> None:
    # No vol can make a call worth more than DF*F.
    _, ceiling = european_price_bounds(_F, 100.0, _DF, "C")
    result = _solve_call(ceiling + 1.0, 100.0)
    assert result.iv is None
    assert result.status == STATUS_ABOVE_MAX


def test_price_needing_vol_beyond_search_range_is_labeled_above_max() -> None:
    # A price just under the DF*F ceiling is below the theoretical max (so not the
    # "impossible" branch) but needs a vol above the 500% search ceiling to reach.
    # That is unresolvable, and the solver labels it above_max with no vol.
    _, ceiling = european_price_bounds(_F, 100.0, _DF, "C")
    result = _solve_call(0.999 * ceiling, 100.0)
    assert result.iv is None
    assert result.status == STATUS_ABOVE_MAX


def test_non_convergence_is_labeled_not_raised() -> None:
    # A one-iteration budget cannot reach a 1e-12 tolerance; brentq reports
    # non-convergence (disp=False), which the solver labels rather than letting raise.
    starved = SolverConfig(version="x", iv_tolerance=1e-15, max_iterations=1)
    point = _SURFACE.points[0]
    result = solve_iv(
        point.call_price, contract_key="K80", forward=_F, strike=point.strike,
        maturity_years=_T, discount_factor=_DF, option_right="C", config=starved,
    )
    assert result.status == STATUS_NON_CONVERGENCE
    assert result.iv is None
    assert result.iterations >= 1


def test_price_exactly_at_intrinsic_implies_zero_vol() -> None:
    # A price at the zero-vol floor has no time value, so the implied vol is exactly 0.
    intrinsic, _ = european_price_bounds(_F, 80.0, _DF, "C")
    result = _solve_call(intrinsic, 80.0)
    assert result.status == STATUS_CONVERGED
    assert result.iv == pytest.approx(0.0, abs=1e-9)
    assert result.total_variance == pytest.approx(0.0, abs=1e-12)


def test_deep_otm_near_zero_vega_still_converges() -> None:
    # A deep-OTM call (strike 300, F 100) has a tiny price and near-zero vega, the
    # ill-conditioned regime. The solver must still recover the vol it was priced at.
    sigma_true = 0.45
    strike = 300.0
    price = black_call(_F, strike, _T, sigma_true, _DF)
    result = _solve_call(price, strike)
    assert result.status == STATUS_CONVERGED
    assert result.iv == pytest.approx(sigma_true, abs=1e-4)


# --------------------------------------------------------------------------- #
# American inversion via the chosen pricer (the engine-agnostic primitive)     #
# --------------------------------------------------------------------------- #
def test_american_inversion_via_the_lattice_pricer() -> None:
    # The scalar primitive inverts *any* monotone pricer. Price an American put with
    # the lattice at a known vol, then invert that same lattice pricer to recover it.
    # (The lattice is the independent oracle; brentq is the solver — different code.)
    spot, strike, rate, vol_true, mat = 100.0, 110.0, 0.05, 0.30, 0.5
    df = math.exp(-rate * mat)

    def american_put_price(vol: float) -> float:
        state = from_spot(spot=spot, strike=strike, maturity_years=mat, volatility=vol,
                          discount_factor=df, option_right="P", carry=rate,
                          exercise_style="american")
        return price_american(state).price

    target = american_put_price(vol_true)
    # American put bounds: worth at least undiscounted intrinsic, at most the strike.
    intrinsic = max(strike - spot, 0.0)
    outcome = solve_implied_vol_scalar(
        target, american_put_price, intrinsic=intrinsic, ceiling=strike, config=CFG
    )
    assert outcome.status == STATUS_CONVERGED
    assert outcome.converged
    assert outcome.iv == pytest.approx(vol_true, abs=1e-3)  # lattice discretization tolerance


# --------------------------------------------------------------------------- #
# Batch wrapper                                                               #
# --------------------------------------------------------------------------- #
def test_batch_solves_each_independently_and_preserves_order() -> None:
    requests = tuple(
        IvRequest(
            target_price=point.call_price, contract_key=f"K{point.strike:g}", forward=_F,
            strike=point.strike, maturity_years=_T, discount_factor=_DF, option_right="C",
        )
        for point in _SURFACE.points
    )
    results = solve_iv_batch(requests, config=CFG)
    assert len(results) == len(_SURFACE.points)
    for result, point in zip(results, _SURFACE.points, strict=True):
        assert result.contract_key == f"K{point.strike:g}"
        assert result.iv == pytest.approx(point.sigma, abs=1e-7)


def test_batch_isolates_a_pathological_quote() -> None:
    # One impossible quote among good ones yields its own labeled failure, not a
    # batch-wide crash.
    good = _SURFACE.points[0]
    requests = (
        IvRequest(good.call_price, f"K{good.strike:g}", _F, good.strike, _T, _DF, "C"),
        IvRequest(1e9, "K-bad", _F, 100.0, _T, _DF, "C"),  # absurd price -> above max
    )
    results = solve_iv_batch(requests, config=CFG)
    assert results[0].status == STATUS_CONVERGED
    assert results[1].status == STATUS_ABOVE_MAX


# --------------------------------------------------------------------------- #
# Put bounds and a put recovery                                               #
# --------------------------------------------------------------------------- #
def test_put_bounds_and_recovery() -> None:
    point = _SURFACE.points[4]  # strike 120, an ITM put
    intrinsic, ceiling = european_price_bounds(_F, point.strike, _DF, "P")
    assert intrinsic == pytest.approx(_DF * max(point.strike - _F, 0.0))
    assert ceiling == pytest.approx(_DF * point.strike)
    result = solve_iv(
        point.put_price, contract_key="P120", forward=_F, strike=point.strike,
        maturity_years=_T, discount_factor=_DF, option_right="P", config=CFG,
    )
    assert result.iv == pytest.approx(point.sigma, abs=1e-7)


# --------------------------------------------------------------------------- #
# Contract adapter                                                            #
# --------------------------------------------------------------------------- #
def test_iv_point_is_a_valid_stamped_contract() -> None:
    from datetime import UTC, datetime

    point = _SURFACE.points[1]
    result = _solve_call(point.call_price, point.strike)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    emitted = iv_point(result, snapshot_ts=snap_ts, source_snapshot_ts=snap_ts,
                       calc_ts=snap_ts, config_hashes={"cfg": "cfg-hash-0"})
    assert isinstance(emitted, IvPoint)
    validate(emitted)  # raises if any contract field rule is violated
    assert table_for_contract(IvPoint) == "iv_points"
    assert emitted.implied_vol == pytest.approx(point.sigma, abs=1e-7)
    assert emitted.diagnostics.converged is True
    assert emitted.diagnostics.status == "converged"


def test_iv_point_refuses_an_unconverged_solve() -> None:
    from datetime import UTC, datetime

    _, ceiling = european_price_bounds(_F, 100.0, _DF, "C")
    failed = _solve_call(ceiling + 1.0, 100.0)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="unconverged"):
        iv_point(failed, snapshot_ts=snap_ts, source_snapshot_ts=snap_ts,
                 calc_ts=snap_ts, config_hashes={"cfg": "c"})
