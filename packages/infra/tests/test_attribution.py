"""2C — by-Greek PnL attribution: term split, residual vs full reprice, book additivity.

Independent oracle (never the code under test): the per-Greek Taylor terms for a fixture
with hand-chosen ``(Δ,Γ,Vega,Θ,S,spot_shock,vol_shock,time_shock,mult,qty)`` are derived in
the test comments; the full-reprice residuals reuse the ``test_determinism_risk`` oracle
totals (hand-coded generalized Black-Scholes-Merton cross-checked against QuantLib). The
full reprice is the truth; the Taylor split is the explanation; the residual is its honest
accuracy — bounded-and-reported for a small shock, material-and-labeled for a large one.

Golden + cross-process determinism mirror ``test_determinism_risk``: recompute the committed
attribution and compare, and recompute the stamp hashes in a fresh interpreter with
``PYTHONHASHSEED`` unset. Regenerate the golden deliberately (the diff is then reviewed)
with the one shared flag (``conftest.golden_artifact``):

    uv run pytest packages/infra/tests/test_attribution.py -k golden --regen-golden
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref
from algotrading.infra.contracts import ContractValidationError, ScenarioAttribution
from algotrading.infra.pricing import PriceGreeks, price
from algotrading.infra.risk import (
    BOOK_CONTRACT_KEY,
    RISK_ENGINE_VERSION,
    AttributionConfig,
    PositionRisk,
    RealizedAttributionError,
    Scenario,
    attribute_book,
    attribute_line,
    attribute_realized_book,
    attribute_realized_line,
    book_attribution_result,
    line_attribution_result,
    local_approx_pnl,
    position_risk,
    pricing_state_for,
    taylor_terms,
    terms_from_move,
)
from algotrading.infra.storage import ParquetStore
from fixtures.positions import CALL_100, RISK_VALUATIONS, risk_positions
from fixtures.records import make_stamp

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
DEFAULT_CFG = AttributionConfig.defaults()

# Explicit oracle scenarios reused from test_determinism_risk (full-reprice totals there).
S_SMALL = Scenario("spot_down_5", "spot", -0.05, 0.0, 0.0)
S_LARGE = Scenario("spot_down_25", "spot", -0.25, 0.0, 0.0)
S_ROLL = Scenario("roll_1d", "time", 0.0, 0.0, 1.0 / 365.0)
S_ZERO = Scenario("flat", "spot", 0.0, 0.0, 0.0)

_GOLDEN_PATH = Path(__file__).parent / "golden" / "attribution_pf_risk.json"
_TESTS_DIR = str(Path(__file__).resolve().parent)


def pf_lines() -> list[PositionRisk]:
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


# --- Refactor-equivalence: the split sums to the lumped Taylor path ----------
def test_terms_sum_to_lumped_taylor() -> None:
    # The split and the lump share one arithmetic home (taylor_terms), so for the default
    # config the four terms sum to local_approx_pnl EXACTLY (==, not approx).
    line = pf_lines()[0]
    terms = taylor_terms(
        line.greeks, spot=line.valuation.spot, scale=line.scale, scenario=S_SMALL, config=DEFAULT_CFG
    )
    assert terms.delta_pnl + terms.gamma_pnl + terms.vega_pnl + terms.theta_pnl == local_approx_pnl(
        line, S_SMALL
    )
    assert terms.total == local_approx_pnl(line, S_SMALL)


# --- Independent per-term oracle ---------------------------------------------
def test_each_term_matches_hand_value() -> None:
    # Hand-chosen Greeks and shock, terms derived here (not read from the code):
    #   S=100, spot_shock=0.05  -> dS = 100*0.05 = 5.0
    #   vol_shock=0.02, time_shock=0.01 (years), mult=100, qty=10 -> scale = 1000
    #   Δ=0.6, Γ=0.05, Vega=12.0, Θ=-5.0
    #   delta_pnl = 0.6 * 5.0 * 1000           = 3000.0
    #   gamma_pnl = 0.5 * 0.05 * 5.0**2 * 1000 = 625.0
    #   vega_pnl  = 12.0 * 0.02 * 1000         = 240.0
    #   theta_pnl = -5.0 * 0.01 * 1000         = -50.0   (default 365 day-count -> factor 1)
    greeks = PriceGreeks(price=10.0, delta=0.6, gamma=0.05, vega=12.0, theta=-5.0, rho=0.0)
    scenario = Scenario("oracle", "spot", 0.05, 0.02, 0.01)
    terms = taylor_terms(greeks, spot=100.0, scale=1000.0, scenario=scenario, config=DEFAULT_CFG)
    assert terms.delta_pnl == pytest.approx(3000.0)
    assert terms.gamma_pnl == pytest.approx(625.0)
    assert terms.vega_pnl == pytest.approx(240.0)
    assert terms.theta_pnl == pytest.approx(-50.0)
    assert terms.total == pytest.approx(3815.0)
    # The pure-spot scenario carries no rate/vol/cross move, so the second-order terms
    # are exactly zero — which is why extending the split leaves the pure-spot golden put.
    assert (terms.rho_pnl, terms.vanna_pnl, terms.volga_pnl) == (0.0, 0.0, 0.0)


# --- Second-order + rate terms: hand value over an explicit move --------------
def test_second_order_terms_match_hand_value() -> None:
    # Hand-chosen Greeks and an explicit move with a vol, time AND rate component:
    #   dS=5.0, dvol=0.02, dt=0.01yr, dr=0.001, scale=1000
    #   Δ=0.6 Γ=0.05 Vega=12 Θ=-5 Rho=8.0 Vanna=0.4 Volga=2.0
    #   rho_pnl   = 8.0 * 0.001 * 1000            =   8.0
    #   vanna_pnl = 0.4 * 5.0 * 0.02 * 1000       =  40.0
    #   volga_pnl = 0.5 * 2.0 * 0.02**2 * 1000    =   0.4
    #   (first four as in the hand oracle above: 3000 + 625 + 240 - 50)
    greeks = PriceGreeks(
        price=10.0, delta=0.6, gamma=0.05, vega=12.0, theta=-5.0, rho=8.0,
        vanna=0.4, volga=2.0, charm=-0.03,
    )
    terms = terms_from_move(
        greeks, scale=1000.0, d_spot=5.0, d_vol=0.02, d_time=0.01, d_rate=0.001, config=DEFAULT_CFG
    )
    assert terms.rho_pnl == pytest.approx(8.0)
    assert terms.vanna_pnl == pytest.approx(40.0)
    assert terms.volga_pnl == pytest.approx(0.4)
    assert terms.total == pytest.approx(3863.4)


# --- A combined spot+vol scenario: the 2nd-order terms shrink the residual ----
def test_combined_scenario_second_order_terms_shrink_the_residual() -> None:
    # On a joint spot-and-vol move the Vanna and Volga terms are real and non-zero, so the
    # 7-term split explains strictly more of the full reprice than the old 4-term split —
    # the residual (the honesty meter) shrinks. The scenario grid holds rates fixed, so the
    # rate term stays zero here (it is the realized path that drives it).
    line = next(ln for ln in pf_lines() if ln.contract_key == "AAPL|OPT|C|100")
    combined = Scenario("crash_spot_vol", "combined", -0.05, 0.03, 0.0)
    la = attribute_line(line, combined, DEFAULT_CFG)
    terms = la.terms
    assert terms.vanna_pnl != 0.0
    assert terms.volga_pnl != 0.0
    assert terms.rho_pnl == 0.0
    approx_first_order = terms.delta_pnl + terms.gamma_pnl + terms.vega_pnl + terms.theta_pnl
    residual_first_order = la.full_reprice_pnl - approx_first_order
    # Adding the second-order terms moves the explanation closer to the full-reprice oracle.
    assert abs(la.residual) < abs(residual_first_order)


# --- Realized day-over-day attribution (TARGET §5.2) -------------------------
def _start_line() -> PositionRisk:
    """A start-of-day (t-1) line for the long-10 C100 holding."""
    return position_risk(portfolio_id="pf-risk", quantity=10.0, valuation=CALL_100)


def test_realized_line_residual_is_full_reprice_minus_terms() -> None:
    start = _start_line()
    end = dataclasses.replace(
        CALL_100,
        spot=CALL_100.spot * 1.01,
        volatility=CALL_100.volatility + 0.01,
        maturity_years=CALL_100.maturity_years - 1.0 / 365.0,
    )
    realized = attribute_realized_line(start, end, DEFAULT_CFG)
    # The oracle is the honest reprice of the HELD line, start price to end price.
    end_price = price(pricing_state_for(end)).price
    assert realized.full_reprice_pnl == pytest.approx((end_price - start.greeks.price) * start.scale)
    assert realized.residual == pytest.approx(realized.full_reprice_pnl - realized.terms.total)


def test_realized_terms_use_start_of_day_greeks_only() -> None:
    # Look-ahead discipline: the decomposition is a pure function of the START-of-day
    # Greeks and the realized move — never today's Greeks. Pinned by equality with the one
    # arithmetic home evaluated on the start Greeks.
    start = _start_line()
    end = dataclasses.replace(
        CALL_100,
        spot=CALL_100.spot * 1.02,
        volatility=CALL_100.volatility + 0.015,
        maturity_years=CALL_100.maturity_years - 1.0 / 365.0,
    )
    realized = attribute_realized_line(start, end, DEFAULT_CFG)
    expected = terms_from_move(
        start.greeks,
        scale=start.scale,
        d_spot=end.spot - start.valuation.spot,
        d_vol=end.volatility - start.valuation.volatility,
        d_time=start.valuation.maturity_years - end.maturity_years,
        d_rate=end.implied_rate - start.valuation.implied_rate,
        config=DEFAULT_CFG,
    )
    assert realized.terms == expected
    # delta_pnl uses the t-1 delta, not the end-of-day delta.
    d_spot = end.spot - start.valuation.spot
    assert realized.terms.delta_pnl == pytest.approx(start.greeks.delta * d_spot * start.scale)


def test_realized_rho_term_is_driven_by_the_rate_move() -> None:
    # A realized rate change (a different discount factor) drives the Rho term — the term
    # the scenario grid can never produce because it holds rates fixed.
    start = _start_line()
    end = dataclasses.replace(CALL_100, discount_factor=CALL_100.discount_factor * 0.999)
    realized = attribute_realized_line(start, end, DEFAULT_CFG)
    d_rate = end.implied_rate - start.valuation.implied_rate
    assert d_rate != 0.0
    assert realized.terms.rho_pnl == pytest.approx(start.greeks.rho * d_rate * start.scale)
    assert realized.terms.rho_pnl != 0.0


def test_realized_line_rejects_a_mismatched_contract() -> None:
    start = _start_line()
    other = RISK_VALUATIONS["AAPL|OPT|P|100"]  # a different contract than the start line
    with pytest.raises(RealizedAttributionError):
        attribute_realized_line(start, other, DEFAULT_CFG)


def test_realized_book_is_term_wise_sum_of_lines() -> None:
    starts = pf_lines()
    ends = {
        ln.contract_key: dataclasses.replace(
            RISK_VALUATIONS[ln.contract_key],
            spot=RISK_VALUATIONS[ln.contract_key].spot * 1.01,
            volatility=RISK_VALUATIONS[ln.contract_key].volatility + 0.01,
        )
        for ln in starts
    }
    book = attribute_realized_book(starts, ends, DEFAULT_CFG)
    per_line = [attribute_realized_line(ln, ends[ln.contract_key], DEFAULT_CFG) for ln in starts]
    assert book.terms.total == pytest.approx(math.fsum(a.terms.total for a in per_line))
    assert book.full_reprice_pnl == pytest.approx(math.fsum(a.full_reprice_pnl for a in per_line))
    assert book.residual == pytest.approx(book.full_reprice_pnl - book.terms.total)


def test_realized_book_rejects_a_missing_end_state() -> None:
    with pytest.raises(RealizedAttributionError):
        attribute_realized_book(pf_lines(), {}, DEFAULT_CFG)


# --- Residual vs full reprice: small within, large material ------------------
def test_residual_is_full_reprice_minus_terms() -> None:
    line = next(ln for ln in pf_lines() if ln.contract_key == "AAPL|OPT|C|100")
    small = attribute_line(line, S_SMALL, DEFAULT_CFG)
    # The residual is exactly full reprice minus the summed contributions.
    assert small.residual == pytest.approx(small.full_reprice_pnl - small.terms.total)
    # Small shock: the decomposition explains the reprice (within tolerance, reported).
    assert small.within_tolerance is True
    assert abs(small.residual) <= max(
        DEFAULT_CFG.residual_abs_tol, DEFAULT_CFG.residual_rel_tol * abs(small.full_reprice_pnl)
    )

    large = attribute_line(line, S_LARGE, DEFAULT_CFG)
    # Large shock: Taylor diverges; the residual is material and reported, NOT an error.
    assert large.residual == pytest.approx(large.full_reprice_pnl - large.terms.total)
    assert large.within_tolerance is False
    assert abs(large.residual) > DEFAULT_CFG.residual_rel_tol * abs(large.full_reprice_pnl)
    # The full reprice stays the oracle regardless of the Taylor accuracy.
    assert large.full_reprice_pnl == pytest.approx(-3942.860, rel=1e-5)


# --- Book attribution is the term-wise sum of its lines ----------------------
def test_book_attribution_is_term_wise_sum_of_lines() -> None:
    lines = pf_lines()
    book = attribute_book(lines, S_SMALL, DEFAULT_CFG)
    # Independent hand-sum: book term == sum of the per-line terms, term by term.
    assert book.terms.delta_pnl == pytest.approx(math.fsum(la.terms.delta_pnl for la in book.lines))
    assert book.terms.gamma_pnl == pytest.approx(math.fsum(la.terms.gamma_pnl for la in book.lines))
    assert book.terms.vega_pnl == pytest.approx(math.fsum(la.terms.vega_pnl for la in book.lines))
    assert book.terms.theta_pnl == pytest.approx(math.fsum(la.terms.theta_pnl for la in book.lines))
    # Book residual == summed per-line residuals (book-additivity of the dollar split).
    assert book.residual == pytest.approx(math.fsum(la.residual for la in book.lines))
    assert len(book.lines) == 3


# --- Reordering invariance (the D-owned risk invariant) ----------------------
def test_attribution_invariant_under_position_reordering() -> None:
    lines = pf_lines()
    forward = attribute_book(lines, S_SMALL, DEFAULT_CFG)
    backward = attribute_book(list(reversed(lines)), S_SMALL, DEFAULT_CFG)
    # math.fsum makes the aggregation order-free, and net_lots sorts the lines, so the whole
    # book record is identical under input reordering.
    assert forward == backward


# --- Config flags move exactly their term, by the expected factor ------------
def test_gamma_norm_flag() -> None:
    greeks = PriceGreeks(price=10.0, delta=0.6, gamma=0.05, vega=12.0, theta=-5.0, rho=0.0)
    scenario = Scenario("oracle", "spot", 0.05, 0.02, 0.01)
    one_dollar = taylor_terms(greeks, spot=100.0, scale=1000.0, scenario=scenario, config=DEFAULT_CFG)
    one_pct_cfg = dataclasses.replace(DEFAULT_CFG, gamma_normalisation="one_pct")
    one_pct = taylor_terms(greeks, spot=100.0, scale=1000.0, scenario=scenario, config=one_pct_cfg)
    # one_pct divides the gamma term by exactly 100; nothing else moves.
    assert one_pct.gamma_pnl == pytest.approx(one_dollar.gamma_pnl / 100.0)
    assert one_pct.delta_pnl == one_dollar.delta_pnl
    assert one_pct.vega_pnl == one_dollar.vega_pnl
    assert one_pct.theta_pnl == one_dollar.theta_pnl
    # The monetized field names follow the dollar/_pnl convention (ADR 0029) — never cash_*.
    field_names = {f.name for f in dataclasses.fields(ScenarioAttribution)}
    assert {"delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl"} <= field_names
    assert not any(name.startswith("cash_") for name in field_names)


def test_theta_daycount_flag() -> None:
    greeks = PriceGreeks(price=10.0, delta=0.6, gamma=0.05, vega=12.0, theta=-5.0, rho=0.0)
    scenario = Scenario("roll", "time", 0.0, 0.0, 0.01)  # a non-zero time roll
    calendar = taylor_terms(greeks, spot=100.0, scale=1000.0, scenario=scenario, config=DEFAULT_CFG)
    trading_cfg = dataclasses.replace(DEFAULT_CFG, theta_day_count=252)
    trading = taylor_terms(greeks, spot=100.0, scale=1000.0, scenario=scenario, config=trading_cfg)
    # 252 re-expresses the theta term by exactly 365/252; nothing else moves.
    assert trading.theta_pnl == pytest.approx(calendar.theta_pnl * (365.0 / 252.0))
    assert trading.delta_pnl == calendar.delta_pnl
    assert trading.gamma_pnl == calendar.gamma_pnl
    assert trading.vega_pnl == calendar.vega_pnl


# --- Edge cases (the floor) --------------------------------------------------
def test_empty_book_is_zero_not_a_crash() -> None:
    book = attribute_book([], S_SMALL, DEFAULT_CFG)
    assert book.lines == ()
    assert book.terms.total == 0.0
    assert book.full_reprice_pnl == 0.0
    assert book.residual == 0.0
    assert book.within_tolerance is True


def test_single_line_book_equals_its_one_line() -> None:
    line = next(ln for ln in pf_lines() if ln.contract_key == "AAPL|OPT|P|100")
    book = attribute_book([line], S_SMALL, DEFAULT_CFG)
    single = attribute_line(line, S_SMALL, DEFAULT_CFG)
    assert book.terms == single.terms
    assert book.residual == pytest.approx(single.residual)
    # The per-line lumped contribution is the sum of its four terms.
    assert single.approx_pnl == single.terms.total
    assert book.approx_pnl == pytest.approx(single.approx_pnl)


def test_zero_shock_scenario_is_all_zero() -> None:
    line = pf_lines()[0]
    attr = attribute_line(line, S_ZERO, DEFAULT_CFG)
    assert attr.terms.total == 0.0
    assert attr.full_reprice_pnl == pytest.approx(0.0, abs=1e-9)
    assert attr.residual == pytest.approx(0.0, abs=1e-9)
    assert attr.within_tolerance is True


def test_non_finite_greek_is_a_labeled_diagnostic() -> None:
    # A non-finite contribution must never read as silent agreement (mirror reconciliation).
    line = pf_lines()[0]
    broken = dataclasses.replace(line, greeks=dataclasses.replace(line.greeks, gamma=math.nan))
    attr = attribute_line(broken, S_SMALL, DEFAULT_CFG)
    assert math.isnan(attr.terms.gamma_pnl)
    assert attr.within_tolerance is False
    assert attr.diagnostic  # a non-empty label, not silent agreement


def test_degenerate_scale_zero_quantity() -> None:
    zero_line = position_risk(portfolio_id="pf-risk", quantity=0.0, valuation=CALL_100)
    attr = attribute_line(zero_line, S_LARGE, DEFAULT_CFG)
    assert attr.terms.total == 0.0
    assert attr.full_reprice_pnl == 0.0
    assert attr.residual == 0.0
    assert attr.within_tolerance is True


# --- Seam: round-trip through storage, malformed rejected --------------------
def _stamp(contract_key: str) -> ProvenanceStamp:
    # Exact historical parameters, passed explicitly: the committed attribution golden
    # pins the stamp hashes these produce.
    return make_stamp(
        (source_ref("market_state_snapshots", TS, contract_key),),
        calc_ts=TS,
        code_version=RISK_ENGINE_VERSION,
        config_hashes={"scenarios": "cfg-hash-0", "attribution": DEFAULT_CFG.version},
        source_timestamps=(TS,),
    )


def make_line_attribution_result() -> ScenarioAttribution:
    line = next(ln for ln in pf_lines() if ln.contract_key == "AAPL|OPT|C|100")
    attr = attribute_line(line, S_SMALL, DEFAULT_CFG)
    return line_attribution_result(
        attr,
        valuation_ts=TS,
        scenario_version="scn-1",
        source_snapshot_ts=TS,
        provenance=_stamp(line.contract_key),
    )


def make_book_attribution_result() -> ScenarioAttribution:
    book = attribute_book(pf_lines(), S_SMALL, DEFAULT_CFG)
    return book_attribution_result(
        book,
        valuation_ts=TS,
        scenario_version="scn-1",
        source_snapshot_ts=TS,
        provenance=_stamp(BOOK_CONTRACT_KEY),
    )


@pytest.mark.parametrize("factory", [make_line_attribution_result, make_book_attribution_result])
def test_attribution_seam_round_trips(factory: Any, tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    record = factory()
    store.write("scenario_attributions", [record])
    read_back = store.read("scenario_attributions")
    assert read_back == [record]
    assert read_back[0].provenance.stamp_hash == record.provenance.stamp_hash


def test_malformed_attribution_is_rejected_by_validation(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    malformed = dataclasses.replace(make_line_attribution_result(), gamma_pnl=math.nan)
    with pytest.raises(ContractValidationError) as info:
        store.write("scenario_attributions", [malformed])
    assert info.value.field == "gamma_pnl"


def test_book_and_line_share_a_key_without_colliding() -> None:
    # The book sentinel rides in contract_key, so the book record and any per-line record
    # have distinct primary keys even at the same (valuation_ts, portfolio, scenario).
    line_rec = make_line_attribution_result()
    book_rec = make_book_attribution_result()
    assert book_rec.contract_key == BOOK_CONTRACT_KEY
    assert line_rec.contract_key != book_rec.contract_key
    assert book_rec.level == "book"
    assert line_rec.level == "position"


# --- Golden artifact + cross-process determinism -----------------------------
def compute_attribution_summary() -> dict[str, Any]:
    """Attribute the pf-risk book under a small and a large scenario; summarize the output.

    Shared by the golden test, the byte-identical repeat, and the cross-process subprocess.
    """
    lines = pf_lines()
    out: dict[str, Any] = {"attribution_version": DEFAULT_CFG.version}
    for tag, scenario in (("small", S_SMALL), ("large", S_LARGE)):
        book = attribute_book(lines, scenario, DEFAULT_CFG)
        book_rec = book_attribution_result(
            book,
            valuation_ts=TS,
            scenario_version="scn-1",
            source_snapshot_ts=TS,
            provenance=_stamp(BOOK_CONTRACT_KEY),
        )
        out[tag] = {
            "delta_pnl": book.terms.delta_pnl,
            "gamma_pnl": book.terms.gamma_pnl,
            "vega_pnl": book.terms.vega_pnl,
            "theta_pnl": book.terms.theta_pnl,
            "approx_pnl": book.approx_pnl,
            "full_reprice_pnl": book.full_reprice_pnl,
            "residual": book.residual,
            "within_tolerance": book.within_tolerance,
            "lines": {
                la.contract_key: [la.terms.delta_pnl, la.terms.gamma_pnl, la.terms.vega_pnl,
                                  la.terms.theta_pnl, la.residual]
                for la in book.lines
            },
            "stamp_hash": book_rec.provenance.stamp_hash,
        }
    return out


def test_attribution_golden_byte_identical(golden_artifact: Any) -> None:
    summary = compute_attribution_summary()
    golden = golden_artifact(_GOLDEN_PATH, summary)
    assert summary["attribution_version"] == golden["attribution_version"]
    for tag in ("small", "large"):
        got, want = summary[tag], golden[tag]
        assert got["stamp_hash"] == want["stamp_hash"]
        assert got["within_tolerance"] == want["within_tolerance"]
        for key in ("delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl", "approx_pnl",
                    "full_reprice_pnl", "residual"):
            assert got[key] == pytest.approx(want[key], rel=1e-9)
        for contract_key, terms in want["lines"].items():
            assert got["lines"][contract_key] == pytest.approx(terms, rel=1e-9)


def test_repeated_attribution_is_byte_identical() -> None:
    assert compute_attribution_summary() == compute_attribution_summary()


_SUBPROCESS_SCRIPT = """
import json
from test_attribution import compute_attribution_summary
print(json.dumps(compute_attribution_summary()))
"""


def test_attribution_hashes_are_stable_across_processes() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([_TESTS_DIR, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env.pop("PYTHONHASHSEED", None)
    completed = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True, text=True, env=env, check=True,
    )
    other = json.loads(completed.stdout)
    here = compute_attribution_summary()
    for tag in ("small", "large"):
        assert other[tag]["stamp_hash"] == here[tag]["stamp_hash"]
        assert other[tag]["residual"] == here[tag]["residual"]
