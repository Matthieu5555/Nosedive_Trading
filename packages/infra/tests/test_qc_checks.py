"""The ten QC checks plus anomaly detection: pass, fail, specificity, edge floor.

For every check there is a passing fixture and a failing fixture. On the failing one
the test asserts the full ``QcResult`` shape — status, severity, measured_value,
threshold_version — AND that the context payload names the *specific* failing
object: the exact maturity, quote, underlying, or solver, not merely that context is
non-empty. A generic banner is the failure mode the framework exists to prevent, so
the specificity assertion is the load-bearing one (tasks/TESTING.md: name the case).

Expected pass/fail outcomes are derived from the thresholds by hand (see the comment
on each case), never by running the check first. Edge cases — empty input, single
element, the value exactly on the boundary, a degenerate shape — are exercised
explicitly. Named producer fixtures (``fixtures.positions.CALL_100``) are preferred
over inline literals; the few pathological inputs a producer fixture does not cover
are built by qc-test-owned helpers below.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime
from typing import Any

import pytest
from algotrading.core.config import GridQcConfig, QcThresholdConfig
from algotrading.infra.contracts import MarketStateSnapshot, QcResult
from algotrading.infra.forwards import ForwardEstimate, ParityLine
from algotrading.infra.iv import (
    STATUS_ABOVE_MAX,
    STATUS_BELOW_INTRINSIC,
    STATUS_CONVERGED,
    STATUS_NON_CONVERGENCE,
    IvResult,
)
from algotrading.infra.pricing import PriceGreeks
from algotrading.infra.qc import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    ContractKeyMismatchError,
    EmptyBaselineError,
    check_calendar_sanity,
    check_collector_continuity,
    check_delta_band_completeness,
    check_forward_stability,
    check_greek_sanity,
    check_iv_solver_convergence,
    check_option_chain_coverage,
    check_parity_residual,
    check_scenario_completeness,
    check_surface_fit_error,
    check_tenor_coverage_floor,
    check_underlying_quote_health,
    deserialize_context,
    detect_anomaly,
    robust_z_score,
)
from algotrading.infra.risk import (
    BrokerGreeks,
    ContractValuationInput,
    PositionRisk,
    position_risk,
)
from algotrading.infra.snapshots import AssessedSnapshot, QuoteAssessment, SnapshotBatch
from algotrading.infra.surfaces import CalendarViolation, SliceFit
from fixtures.positions import CALL_100, PUT_100
from fixtures.records import make_record

# --- shared injected clock and thresholds ---------------------------------------
RUN_ID = "qc-run-2026-06-02"
RUN_TS = datetime(2026, 6, 2, 23, 0, tzinfo=UTC)

# A concrete config: version stamps the result, the three cross-cutting cut-offs are
# explicit so every threshold-boundary case below is hand-derivable from these.
QC_CONFIG = QcThresholdConfig(
    version="qc-threshold-1.0.0",
    max_spread_pct=0.05,
    max_quote_age_seconds=30.0,
    min_chain_count=4,
)
# The checks consume the typed, hashed QcThresholdConfig directly (M37) — no bundle
# wrapper in between.
THRESHOLDS = QC_CONFIG


# --- qc-test-owned builders for inputs no producer fixture covers ----------------
@dataclasses.dataclass(frozen=True)
class _FakeSummary:
    """A minimal collector summary satisfying ``qc.CollectorContinuityInput``.

    The packages ``collectors`` plane (C1) does not export a summary type yet, so the
    check declares its input as a structural Protocol; this is the test's stand-in,
    carrying exactly the four fields the continuity check reads.
    """

    session_id: str
    gap_count: int
    subscribed_count: int
    covered_count: int


def _summary(
    *,
    session_id: str = "sess-1",
    gap_count: int = 0,
    subscribed: int = 100,
    covered: int = 100,
) -> _FakeSummary:
    return _FakeSummary(
        session_id=session_id,
        gap_count=gap_count,
        subscribed_count=subscribed,
        covered_count=covered,
    )


def _snapshot(instrument_key: str, *, underlying: str, spread_pct: float) -> MarketStateSnapshot:
    return make_record(
        "market_state_snapshots",
        snapshot_ts=RUN_TS,
        instrument_key=instrument_key,
        reference_spot=100.0,
        bid=100.0 - spread_pct * 50.0,
        ask=100.0 + spread_pct * 50.0,
        last=100.0,
        spread_pct=spread_pct,
        flags=(),
        trade_date=date(2026, 6, 2),
        underlying=underlying,
    )


def _assessed(
    instrument_key: str, *, underlying: str, spread_pct: float, status: str = "usable"
) -> AssessedSnapshot:
    return AssessedSnapshot(
        snapshot=_snapshot(instrument_key, underlying=underlying, spread_pct=spread_pct),
        assessment=QuoteAssessment(status=status, reasons=()),
    )


def _forward(
    *,
    underlying: str = "AAPL",
    maturity: float = 0.25,
    residual_mad: float,
    forward: float = 100.0,
    confidence: float = 1.0,
    quality_label: str = "good",
    reason_code: str = "ok",
) -> ForwardEstimate:
    return ForwardEstimate(
        underlying=underlying,
        maturity_years=maturity,
        forward=forward,
        discount_factor=0.99,
        spot=forward,
        implied_rate=0.01,
        implied_carry=0.0,
        implied_dividend=0.0,
        method="regression",
        reason_code=reason_code,
        quality_label=quality_label,
        confidence=confidence,
        candidate_count=8,
        used_count=8,
        rejected_count=0,
        residual_mad=residual_mad,
        points=(),
    )


def _parity_line(residuals: tuple[float, ...], *, forward: float = 100.0) -> ParityLine:
    return ParityLine(
        intercept=0.0,
        slope=0.99,
        discount_factor=0.99,
        forward=forward,
        residuals=residuals,
    )


def _iv_result(contract_key: str, status: str) -> IvResult:
    converged = status == STATUS_CONVERGED
    return IvResult(
        contract_key=contract_key,
        iv=0.2 if converged else None,
        k=0.0,
        total_variance=0.01 if converged else None,
        status=status,
        iterations=5,
        residual=0.0 if converged else 1.0,
        model="black",
        bracket_low=0.01,
        bracket_high=5.0,
        forward=100.0,
        strike=100.0,
        maturity_years=0.25,
    )


def _slice_fit(*, underlying: str = "AAPL", maturity: float = 0.25, rmse: float) -> SliceFit:
    return SliceFit(
        underlying=underlying,
        maturity_years=maturity,
        expiry_date=date(2026, 9, 1),
        day_count="ACT/365",
        method="svi",
        svi=None,
        rmse=rmse,
        n_points=10,
        arb_free=True,
        bound_hits=(),
        butterfly_violations=(),
        nonparametric_ks=(),
        nonparametric_ws=(),
        raw_points=(),
    )


def _position(
    valuation: ContractValuationInput = CALL_100, *, greeks: PriceGreeks | None = None
) -> PositionRisk:
    line = position_risk(portfolio_id="P", quantity=1.0, valuation=valuation)
    if greeks is not None:
        line = dataclasses.replace(line, greeks=greeks)
    return line


def _assert_full_shape(
    result: QcResult,
    *,
    check_name: str,
    status: str,
    severity: str,
) -> dict[str, Any]:
    """Assert the four QcResult facets and return the parsed context for specificity."""
    assert result.check_name == check_name
    assert result.qc_status == status
    assert result.severity == severity
    assert result.threshold_version == QC_CONFIG.version
    assert result.run_id == RUN_ID
    assert result.run_ts == RUN_TS
    return deserialize_context(result.context)


# ================================================================================
# 1. collector continuity
# ================================================================================
def test_collector_continuity_passes_clean_session() -> None:
    # 0 gaps <= warn_gap_count(1) and full coverage >= 0.95 -> pass (derived from thresholds).
    result = check_collector_continuity(
        _summary(gap_count=0), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_collector_continuity_fails_and_names_session() -> None:
    # gap_count 6 > max_gap_count(5) -> fail. Derived from DEFAULT_MAX_GAP_COUNT.
    result = check_collector_continuity(
        _summary(session_id="sess-bad", gap_count=6),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    context = _assert_full_shape(
        result, check_name="collector_continuity", status=STATUS_FAIL, severity=SEVERITY_CRITICAL
    )
    assert result.measured_value == 6.0
    assert context["failing_session"] == "sess-bad"  # names the exact session
    assert result.target_key == "sess-bad"


def test_collector_continuity_warns_in_gap_band() -> None:
    # 3 gaps: > warn(1) but <= max(5), coverage fine -> warn.
    result = check_collector_continuity(
        _summary(gap_count=3), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_WARN


def test_collector_continuity_fails_on_thin_coverage() -> None:
    # coverage 90/100 = 0.90 < min_coverage_ratio(0.95) -> fail even with zero gaps.
    result = check_collector_continuity(
        _summary(gap_count=0, subscribed=100, covered=90),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL


def test_collector_continuity_boundary_gap_exact_passes() -> None:
    # gap_count exactly == max_gap_count(5) is NOT > max -> not a fail; > warn -> warn.
    result = check_collector_continuity(
        _summary(gap_count=5), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_WARN


def test_collector_continuity_empty_subscription_degenerate() -> None:
    # zero subscribed -> coverage defined as 1.0 (no universe to miss), zero gaps -> pass.
    result = check_collector_continuity(
        _summary(gap_count=0, subscribed=0, covered=0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert deserialize_context(result.context)["coverage_ratio"] == 1.0


# ================================================================================
# 2. underlying quote health
# ================================================================================
def test_quote_health_passes_tight_quote() -> None:
    # spread 0.002 <= max_spread_pct(0.05) -> pass.
    batch = SnapshotBatch(
        assessed=(_assessed("AAPL-STK", underlying="AAPL", spread_pct=0.002),), skipped=()
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_quote_health_fails_and_names_quote() -> None:
    # widest usable spread 0.08 > max_spread_pct(0.05) -> fail; names the worst quote key.
    batch = SnapshotBatch(
        assessed=(
            _assessed("AAPL-STK", underlying="AAPL", spread_pct=0.01),
            _assessed("MSFT-STK", underlying="MSFT", spread_pct=0.08),
        ),
        skipped=(),
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK", "MSFT-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    context = _assert_full_shape(
        result,
        check_name="underlying_quote_health",
        status=STATUS_FAIL,
        severity=SEVERITY_CRITICAL,
    )
    assert result.measured_value == pytest.approx(0.08)
    assert context["failing_quote"] == "MSFT-STK"  # the exact bad quote, not "a quote"
    assert result.target_key == "MSFT-STK"


def test_quote_health_boundary_spread_exact_passes() -> None:
    # spread exactly == max_spread_pct(0.05) is not > max -> pass (boundary inclusive).
    batch = SnapshotBatch(
        assessed=(_assessed("AAPL-STK", underlying="AAPL", spread_pct=0.05),), skipped=()
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_quote_health_ignores_rejected_quotes() -> None:
    # the only wide quote is 'reject' status -> not counted -> pass.
    batch = SnapshotBatch(
        assessed=(_assessed("AAPL-STK", underlying="AAPL", spread_pct=0.5, status="reject"),),
        skipped=(),
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_quote_health_empty_batch_passes() -> None:
    batch = SnapshotBatch(assessed=(), skipped=())
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


# ================================================================================
# 3. option chain coverage
# ================================================================================
_EXPECTED_CHAIN = ("AAPL-C90", "AAPL-C95", "AAPL-C100", "AAPL-C105")  # 4 == min_chain_count


def _chain_batch(present_keys: tuple[str, ...]) -> SnapshotBatch:
    return SnapshotBatch(
        assessed=tuple(
            _assessed(key, underlying="AAPL", spread_pct=0.01) for key in present_keys
        ),
        skipped=(),
    )


def test_chain_coverage_passes_full_chain() -> None:
    # 4 usable >= min_chain_count(4) -> pass (boundary-exact count).
    result = check_option_chain_coverage(
        _chain_batch(_EXPECTED_CHAIN),
        "AAPL",
        _EXPECTED_CHAIN,
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 4.0


def test_chain_coverage_fails_and_names_missing_contracts() -> None:
    # only 2 of 4 present -> 2 < min_chain_count(4) -> fail; names the missing strikes.
    result = check_option_chain_coverage(
        _chain_batch(("AAPL-C90", "AAPL-C95")),
        "AAPL",
        _EXPECTED_CHAIN,
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    context = _assert_full_shape(
        result, check_name="option_chain_coverage", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    assert result.measured_value == 2.0
    assert context["missing_contracts"] == ["AAPL-C100", "AAPL-C105"]  # the exact absent strikes
    assert context["underlying"] == "AAPL"


def test_chain_coverage_empty_batch_lists_all_missing() -> None:
    result = check_option_chain_coverage(
        SnapshotBatch(assessed=(), skipped=()),
        "AAPL",
        _EXPECTED_CHAIN,
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    assert deserialize_context(result.context)["missing_contracts"] == sorted(_EXPECTED_CHAIN)


# ================================================================================
# 4. forward stability
# ================================================================================
def test_forward_stability_passes_tight_forward() -> None:
    # rel residual 0.01/100 = 1e-4 <= max_rel(0.01) and confidence 1.0 >= min(0.5) -> pass.
    result = check_forward_stability(
        _forward(residual_mad=0.01), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_forward_stability_fails_and_names_maturity() -> None:
    # rel residual 2.0/100 = 0.02 > max_rel_residual_mad(0.01) -> fail; names underlying + maturity.
    result = check_forward_stability(
        _forward(underlying="SX5E", maturity=0.5, residual_mad=2.0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    context = _assert_full_shape(
        result, check_name="forward_stability", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    # measured value is now the RELATIVE residual (residual_mad / forward).
    assert result.measured_value == pytest.approx(0.02)
    assert context["underlying"] == "SX5E"
    assert context["failing_maturity"] == 0.5  # the exact failing maturity
    assert result.target_key == "SX5E@0.5"


def test_forward_stability_fails_on_low_confidence() -> None:
    # residual fine but confidence 0.3 < min(0.5) -> fail.
    result = check_forward_stability(
        _forward(residual_mad=0.0, confidence=0.3),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL


def test_forward_stability_boundary_mad_exact_passes() -> None:
    # rel residual exactly == max_rel(0.01): 1.0/100 = 0.01 is not > max -> pass.
    result = check_forward_stability(
        _forward(residual_mad=1.0), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_forward_stability_passes_on_index_scale_forward() -> None:
    # Reconciliation (T-qc-residual-units / An-1): the real 2026-06-11 SPX forward carried
    # residual_mad 0.159 on a ~7400 forward -> rel 2.1e-5, which the diagnostic labels "good".
    # The absolute-$ gate FAILed it (0.159 > 0.05); the relative gate agrees with the label.
    result = check_forward_stability(
        _forward(residual_mad=0.159, forward=7400.0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == pytest.approx(0.159 / 7400.0)


def test_forward_stability_fails_a_genuinely_bad_index_forward() -> None:
    # A forward whose MAD is 2.7% of spot is genuinely unstable regardless of index scale.
    result = check_forward_stability(
        _forward(residual_mad=200.0, forward=7400.0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL


# ================================================================================
# 5. parity residual
# ================================================================================
def test_parity_residual_passes_small_residuals() -> None:
    # worst |residual| 0.03 on forward 100 -> rel 3e-4 <= max_rel_parity_residual(0.02) -> pass.
    result = check_parity_residual(
        _parity_line((0.01, -0.03, 0.02)),
        "AAPL",
        0.25,
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS


def test_parity_residual_fails_and_names_maturity_and_index() -> None:
    # worst |residual| 3.0 at index 1 on forward 100 -> rel 0.03 > max_rel(0.02) -> fail.
    result = check_parity_residual(
        _parity_line((0.01, -3.0, 0.05)),
        "AAPL",
        0.75,
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    context = _assert_full_shape(
        result, check_name="parity_residual", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    # measured value is now the RELATIVE worst residual (|residual| / forward).
    assert result.measured_value == pytest.approx(0.03)
    assert context["failing_maturity"] == 0.75
    assert context["worst_residual_index"] == 1  # pins the exact offending strike-pair


def test_parity_residual_passes_on_index_scale_forward() -> None:
    # Reconciliation (T-qc-residual-units / An-2): a clean index slice carries worst parity
    # residual ~2.48 on a ~7400 forward -> rel 3.4e-4. The absolute-$ gate FAILed it (2.48 > 0.10);
    # the relative gate passes it as the good slice it is.
    result = check_parity_residual(
        _parity_line((1.1, -2.48, 0.9), forward=7400.0),
        "SPX",
        0.25,
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == pytest.approx(2.48 / 7400.0)


def test_parity_residual_fails_a_genuinely_broken_index_slice() -> None:
    # A worst parity residual of ~4% of the forward is genuinely broken regardless of scale.
    result = check_parity_residual(
        _parity_line((10.0, -300.0, 5.0), forward=7400.0),
        "SPX",
        0.05,
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL


def test_parity_residual_empty_residuals_passes() -> None:
    # degenerate: no residuals -> worst is 0.0 <= max -> pass, index -1.
    result = check_parity_residual(
        _parity_line(()), "AAPL", 0.25, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert deserialize_context(result.context)["worst_residual_index"] == -1


def test_parity_residual_boundary_exact_passes() -> None:
    # rel |residual| exactly == max_rel(0.02): 2.0/100 = 0.02 is not > max -> pass.
    result = check_parity_residual(
        _parity_line((2.0,)), "AAPL", 0.25, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


# ================================================================================
# 6. IV solver convergence
# ================================================================================
def test_iv_convergence_passes_all_converged() -> None:
    results = [_iv_result(f"AAPL-C{k}", STATUS_CONVERGED) for k in (90, 95, 100)]
    result = check_iv_solver_convergence(
        results, "AAPL@0.25", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_iv_convergence_fails_and_names_solver() -> None:
    # 2 of 10 failed -> ratio 0.20 > max_non_convergence_ratio(0.10) -> fail.
    results = [_iv_result(f"AAPL-C{k}", STATUS_CONVERGED) for k in range(8)]
    results.append(_iv_result("AAPL-C-DEEP-ITM", STATUS_BELOW_INTRINSIC))
    results.append(_iv_result("AAPL-C-FAR-OTM", STATUS_NON_CONVERGENCE))
    result = check_iv_solver_convergence(
        results, "AAPL@0.25", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    context = _assert_full_shape(
        result, check_name="iv_solver_convergence", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    assert result.measured_value == pytest.approx(0.20)
    failing_keys = {entry["contract_key"] for entry in context["failing_solvers"]}
    assert failing_keys == {"AAPL-C-DEEP-ITM", "AAPL-C-FAR-OTM"}  # the exact unsolved contracts


def test_iv_convergence_boundary_ratio_exact_passes() -> None:
    # exactly 1 of 10 failed -> ratio 0.10 == max -> not > max -> pass (boundary inclusive).
    results = [_iv_result(f"AAPL-C{k}", STATUS_CONVERGED) for k in range(9)]
    results.append(_iv_result("AAPL-C-BAD", STATUS_ABOVE_MAX))
    result = check_iv_solver_convergence(
        results, "AAPL@0.25", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_iv_convergence_empty_passes() -> None:
    # no requests -> ratio defined as 0.0 -> pass (nothing to invert, nothing failed).
    result = check_iv_solver_convergence(
        [], "AAPL@0.25", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_iv_convergence_single_failed_element() -> None:
    # one request, it failed -> ratio 1.0 > max -> fail; names that solver.
    result = check_iv_solver_convergence(
        [_iv_result("AAPL-ONLY", STATUS_NON_CONVERGENCE)],
        "AAPL@0.25",
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    assert context["failing_solvers"][0]["contract_key"] == "AAPL-ONLY"


# ================================================================================
# 7. surface fit error
# ================================================================================
def test_surface_fit_passes_tight_fit() -> None:
    # rmse 0.005 <= max_surface_rmse(0.02) -> pass.
    result = check_surface_fit_error(
        _slice_fit(rmse=0.005), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_surface_fit_fails_and_names_maturity() -> None:
    # rmse 0.08 > max_surface_rmse(0.02) -> fail; names underlying + maturity.
    result = check_surface_fit_error(
        _slice_fit(underlying="SX5E", maturity=1.0, rmse=0.08),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    context = _assert_full_shape(
        result, check_name="surface_fit_error", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    assert result.measured_value == pytest.approx(0.08)
    assert context["underlying"] == "SX5E"
    assert context["failing_maturity"] == 1.0  # the exact badly-fit expiry
    assert result.target_key == "SX5E@1"


def test_surface_fit_boundary_rmse_exact_passes() -> None:
    # rmse exactly == max(0.02) is not > max -> pass.
    result = check_surface_fit_error(
        _slice_fit(rmse=0.02), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


# ================================================================================
# 8. calendar sanity
# ================================================================================
def test_calendar_sanity_passes_no_violations() -> None:
    result = check_calendar_sanity(
        [], "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_calendar_sanity_fails_and_names_maturity_pair() -> None:
    # any violation fails; the worst-crossing pair is named.
    violations = [
        CalendarViolation(k=0.0, maturity_short=0.25, maturity_long=0.5, w_short=0.05, w_long=0.04),
        CalendarViolation(k=0.1, maturity_short=0.5, maturity_long=1.0, w_short=0.10, w_long=0.06),
    ]
    result = check_calendar_sanity(
        violations, "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    context = _assert_full_shape(
        result, check_name="calendar_sanity", status=STATUS_FAIL, severity=SEVERITY_CRITICAL
    )
    assert result.measured_value == 2.0
    # worst crossing is the second (w_short-w_long = 0.04 > 0.01): 0.5y vs 1.0y at k=0.1.
    assert context["failing_maturity_short"] == 0.5
    assert context["failing_maturity_long"] == 1.0
    assert context["failing_k"] == pytest.approx(0.1)


def test_calendar_sanity_single_violation() -> None:
    one = CalendarViolation(
        k=0.0, maturity_short=0.25, maturity_long=0.5, w_short=0.05, w_long=0.04
    )
    result = check_calendar_sanity(
        [one],
        "AAPL",
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    assert deserialize_context(result.context)["failing_maturity_short"] == 0.25


# ================================================================================
# 9. Greek sanity
# ================================================================================
def test_greek_sanity_passes_clean_line() -> None:
    # A real priced CALL_100 line: gamma>0, vega>0, call delta in [0,1] -> pass.
    result = check_greek_sanity(
        _position(CALL_100), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_greek_sanity_fails_negative_gamma_and_names_contract() -> None:
    # Force gamma < 0 (impossible for a real option) -> fail; names the contract + greek.
    clean = _position(CALL_100)
    bad = dataclasses.replace(clean.greeks, gamma=-0.01)
    result = check_greek_sanity(
        _position(CALL_100, greeks=bad), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    context = _assert_full_shape(
        result, check_name="greek_sanity", status=STATUS_FAIL, severity=SEVERITY_CRITICAL
    )
    assert result.measured_value == 1.0
    assert context["failing_contract"] == CALL_100.contract_key  # the exact bad contract
    reasons = {breach["reason"] for breach in context["breaches"]}
    assert "negative_gamma" in reasons


def test_greek_sanity_fails_call_delta_out_of_range() -> None:
    clean = _position(CALL_100)
    bad = dataclasses.replace(clean.greeks, delta=1.5)  # a call delta cannot exceed 1
    result = check_greek_sanity(
        _position(CALL_100, greeks=bad), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    reasons = {breach["reason"] for breach in deserialize_context(result.context)["breaches"]}
    assert "call_delta_out_of_range" in reasons


def test_greek_sanity_fails_put_delta_out_of_range() -> None:
    clean = _position(PUT_100)
    bad = dataclasses.replace(clean.greeks, delta=0.5)  # a put delta must be <= 0
    result = check_greek_sanity(
        _position(PUT_100, greeks=bad), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    reasons = {breach["reason"] for breach in deserialize_context(result.context)["breaches"]}
    assert "put_delta_out_of_range" in reasons


def test_greek_sanity_fails_non_finite_greek() -> None:
    clean = _position(CALL_100)
    bad = dataclasses.replace(clean.greeks, vega=math.nan)
    result = check_greek_sanity(
        _position(CALL_100, greeks=bad), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    reasons = {breach["reason"] for breach in deserialize_context(result.context)["breaches"]}
    assert "non_finite" in reasons


def test_greek_sanity_broker_reconcile_breach_named() -> None:
    # Broker delta differs from computed by far more than tolerance(0.001) -> breach.
    line = _position(CALL_100)
    broker = BrokerGreeks(contract_key=CALL_100.contract_key, delta=line.greeks.delta + 0.5)
    result = check_greek_sanity(
        line, broker=broker, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    breaches = deserialize_context(result.context)["breaches"]
    assert any(
        b.get("reason") == "broker_reconcile_breach" and b["greek"] == "delta" for b in breaches
    )


def test_greek_sanity_broker_within_tolerance_passes() -> None:
    line = _position(CALL_100)
    broker = BrokerGreeks(
        contract_key=CALL_100.contract_key,
        delta=line.greeks.delta,
        gamma=line.greeks.gamma,
    )
    result = check_greek_sanity(
        line, broker=broker, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_greek_sanity_contract_key_mismatch_raises_naming_both_keys() -> None:
    # Folds in ADR 0006's deferred reconcile precondition: a broker row for a different
    # contract is a mis-wired join, not a disagreement -> raise, naming both keys.
    line = _position(CALL_100)
    broker = BrokerGreeks(contract_key="WRONG-CONTRACT", delta=0.0)
    with pytest.raises(ContractKeyMismatchError) as excinfo:
        check_greek_sanity(line, broker=broker, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    assert excinfo.value.line_key == CALL_100.contract_key
    assert excinfo.value.broker_key == "WRONG-CONTRACT"


# ================================================================================
# 10. scenario completeness
# ================================================================================
_EXPECTED_CELLS = (
    ("crash", "AAPL-C100"),
    ("crash", "AAPL-P100"),
    ("rally", "AAPL-C100"),
    ("rally", "AAPL-P100"),
)


def test_scenario_completeness_passes_full_grid() -> None:
    result = check_scenario_completeness(
        _EXPECTED_CELLS,
        _EXPECTED_CELLS,
        "PORT-1",
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_scenario_completeness_fails_and_names_missing_cell() -> None:
    # one cell ("rally","AAPL-P100") never produced -> fail; names the exact missing cell.
    produced = (
        ("crash", "AAPL-C100"),
        ("crash", "AAPL-P100"),
        ("rally", "AAPL-C100"),
    )
    result = check_scenario_completeness(
        produced, _EXPECTED_CELLS, "PORT-1", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    context = _assert_full_shape(
        result, check_name="scenario_completeness", status=STATUS_FAIL, severity=SEVERITY_CRITICAL
    )
    assert result.measured_value == 1.0
    assert context["missing_cells"] == [{"scenario_id": "rally", "contract_key": "AAPL-P100"}]
    assert result.target_key == "PORT-1"


def test_scenario_completeness_empty_expected_passes() -> None:
    # nothing expected -> nothing missing -> pass (degenerate empty grid).
    result = check_scenario_completeness(
        (), (), "PORT-1", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


# ================================================================================
# anomaly detection against a rolling baseline
# ================================================================================
def test_anomaly_flags_injected_spike() -> None:
    # Baseline ~50 with tiny spread; an injected 500 is many MADs out -> fail.
    baseline = [50.0, 51.0, 49.0, 50.5, 49.5, 50.0, 51.0, 49.0]
    result = detect_anomaly(
        500.0, baseline, "event_rate", "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    assert context["metric"] == "event_rate"  # names which metric spiked
    assert context["target"] == "AAPL"
    assert result.measured_value > THRESHOLDS.anomaly.mad_multiplier


def test_anomaly_does_not_flag_value_within_baseline() -> None:
    # A value inside the baseline's spread is normal -> pass, NOT flagged.
    baseline = [50.0, 51.0, 49.0, 50.5, 49.5, 50.0, 51.0, 49.0]
    result = detect_anomaly(
        50.2, baseline, "event_rate", "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_anomaly_empty_baseline_raises() -> None:
    with pytest.raises(EmptyBaselineError) as excinfo:
        detect_anomaly(
            10.0, [], "event_rate", "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
        )
    assert excinfo.value.observed == 10.0


def test_robust_z_score_degenerate_zero_spread() -> None:
    # All-equal baseline: equal value scores 0, any departure scores inf (honest scale).
    # Independent oracle: median=5, MAD=0 -> by definition equal->0, unequal->inf.
    assert robust_z_score(5.0, [5.0, 5.0, 5.0]) == 0.0
    assert math.isinf(robust_z_score(6.0, [5.0, 5.0, 5.0]))


def test_anomaly_single_element_baseline() -> None:
    # Single-element baseline has zero MAD; an equal value passes, a far one flags via inf.
    same = detect_anomaly(
        7.0, [7.0], "stale_ratio", "MSFT", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert same.qc_status == STATUS_PASS
    far = detect_anomaly(
        99.0, [7.0], "stale_ratio", "MSFT", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert far.qc_status == STATUS_FAIL


def test_robust_z_score_matches_hand_computed_mad() -> None:
    # Independent oracle (hand-computed): baseline median=15.5, deviations median (MAD)=3.0,
    # scale=1.4826*3.0=4.4478; observed 40 -> |40-15.5|/4.4478 = 5.508...
    baseline = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0]
    assert robust_z_score(40.0, baseline) == pytest.approx(24.5 / (1.4826 * 3.0), rel=1e-9)


# --- ADR 0028: supplementary QC cut-offs are hydrated from config, not `.py` literals ---
def test_supplementary_thresholds_flow_from_config_into_a_check_verdict() -> None:
    # The supplementary cut-offs live in the hashed `qc` config blocks (continuity /
    # forward_engine / fit_tolerance / anomaly), not module literals, and since M37 the
    # checks read the typed QcThresholdConfig directly — no wrapper a stale copy could
    # hide behind. Behavioral pin: overriding a block's cut-off changes the verdict.
    # Oracle, hand-derived from the documented bands: 10 gaps > the default max of 5
    # -> FAIL; under an overridden max of 10 the boundary itself passes the fail test
    # (`>` strictly), but 10 > the overridden warn cut-off of 5 -> WARN.
    overridden = QC_CONFIG.model_copy(
        update={
            "continuity": QC_CONFIG.continuity.model_copy(
                update={"max_gap_count": 10, "warn_gap_count": 5}
            ),
        }
    )
    summary = _summary(gap_count=10)
    failing = check_collector_continuity(
        summary, thresholds=QC_CONFIG, run_id=RUN_ID, run_ts=RUN_TS
    )
    relaxed = check_collector_continuity(
        summary, thresholds=overridden, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert failing.qc_status == STATUS_FAIL
    assert relaxed.qc_status == STATUS_WARN


def test_forward_engine_threshold_override_flips_the_verdict() -> None:
    # Review follow-up: same pin as continuity, per block. rel residual 2.0/100 = 0.02
    # > default max_rel_residual_mad(0.01) -> FAIL; raising the cut-off to 0.05 -> PASS.
    overridden = QC_CONFIG.model_copy(
        update={
            "forward_engine": QC_CONFIG.forward_engine.model_copy(
                update={"max_rel_residual_mad": 0.05}
            ),
        }
    )
    estimate = _forward(residual_mad=2.0)
    failing = check_forward_stability(estimate, thresholds=QC_CONFIG, run_id=RUN_ID, run_ts=RUN_TS)
    relaxed = check_forward_stability(estimate, thresholds=overridden, run_id=RUN_ID, run_ts=RUN_TS)
    assert failing.qc_status == STATUS_FAIL
    assert relaxed.qc_status == STATUS_PASS


def test_fit_tolerance_threshold_override_flips_the_verdict() -> None:
    # rmse 0.08 > default max_surface_rmse(0.02) -> FAIL; raising the cut-off to 0.10 -> PASS.
    overridden = QC_CONFIG.model_copy(
        update={
            "fit_tolerance": QC_CONFIG.fit_tolerance.model_copy(update={"max_surface_rmse": 0.10}),
        }
    )
    fit = _slice_fit(rmse=0.08)
    failing = check_surface_fit_error(fit, thresholds=QC_CONFIG, run_id=RUN_ID, run_ts=RUN_TS)
    relaxed = check_surface_fit_error(fit, thresholds=overridden, run_id=RUN_ID, run_ts=RUN_TS)
    assert failing.qc_status == STATUS_FAIL
    assert relaxed.qc_status == STATUS_PASS


def test_anomaly_threshold_override_flips_the_verdict() -> None:
    # Baseline median 50, MAD 0.75 (scaled 0.75*1.4826 ~= 1.11): 60 sits ~9.0 robust-z
    # out — over the default mad_multiplier(5.0) -> FAIL; raising it to 50 -> PASS.
    overridden = QC_CONFIG.model_copy(
        update={"anomaly": QC_CONFIG.anomaly.model_copy(update={"mad_multiplier": 50.0})}
    )
    baseline = [50.0, 51.0, 49.0, 50.5, 49.5, 50.0, 51.0, 49.0]
    failing = detect_anomaly(
        60.0, baseline, "event_rate", "AAPL", thresholds=QC_CONFIG, run_id=RUN_ID, run_ts=RUN_TS
    )
    relaxed = detect_anomaly(
        60.0, baseline, "event_rate", "AAPL", thresholds=overridden, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert failing.qc_status == STATUS_FAIL
    assert relaxed.qc_status == STATUS_PASS


def test_default_supplementary_thresholds_match_config_defaults() -> None:
    # The shared THRESHOLDS config (QC_CONFIG with default nested blocks) carries exactly
    # the config-default cut-offs the boundary cases above are hand-derived against, on
    # the nested paths the checks read since M37.
    # Independent oracle: the AnomalyQcConfig/etc. schema defaults pinned in platform_config.
    assert THRESHOLDS.continuity.max_gap_count == 5
    assert THRESHOLDS.continuity.warn_gap_count == 1
    assert THRESHOLDS.continuity.min_coverage_ratio == pytest.approx(0.95)
    assert THRESHOLDS.forward_engine.max_rel_residual_mad == pytest.approx(0.01)
    assert THRESHOLDS.forward_engine.min_forward_confidence == pytest.approx(0.5)
    assert THRESHOLDS.forward_engine.max_rel_parity_residual == pytest.approx(0.02)
    assert THRESHOLDS.fit_tolerance.max_non_convergence_ratio == pytest.approx(0.10)
    assert THRESHOLDS.fit_tolerance.max_surface_rmse == pytest.approx(0.02)
    assert THRESHOLDS.anomaly.mad_multiplier == pytest.approx(5.0)


# --- WS 1H: grid-aware QC — per-tenor coverage floor + Δ-band completeness ----------
#
# Expected pass/fail are hand-derived from a grid fixture built by hand from a pinned
# tenor grid and a known delta band (TESTING.md: independent oracle — count by hand, never
# read the verdict back from the check). The test config below pins small, round numbers
# so every boundary case is hand-computable:
#   - per-tenor coverage floor = 3 for every pinned tenor
#   - delta band = [-0.30, +0.30] (the 30Δ-put → ATM → 30Δ-call window)
#   - max interior delta step = 0.35  (so a 3-point [-0.30, 0.0, +0.30] grid spans it:
#     the two gaps are 0.30 each, <= 0.35)
# The pinned tenor grid the tests key on (a 3-tenor subset of the P0.1 grid; the check
# reads it as config, never from the data under test).
GRID_TENORS = ("10d", "1m", "3m")

GRID_QC = GridQcConfig(
    version="grid-qc-test-1",
    tenor_floors={"10d": 3, "1m": 3, "3m": 3},
    band_low_delta=-0.30,
    band_high_delta=0.30,
    max_delta_step=0.35,
)
GRID_THRESHOLDS = QC_CONFIG.model_copy(update={"grid": GRID_QC})


@dataclasses.dataclass(frozen=True)
class _GridPoint:
    """A minimal projected grid cell satisfying ``qc.GridPointInput``.

    WS 1F's ``ProjectedOptionAnalytics`` is the real producer; the grid checks read it
    through the structural Protocol, so this carries the fields they touch. The third
    positional is ``target_delta`` — the signed *band-axis* coordinate the Δ-band check
    spans (ATM at ``0.0``); the realized greek ``delta`` is on the contract but unused by the
    band check, so it defaults here.
    """

    underlying: str
    tenor_label: str
    target_delta: float
    delta: float = 0.0


def _full_tenor(underlying: str, tenor: str) -> list[_GridPoint]:
    """A band-complete, floor-clearing tenor: 3 points at -0.30, 0.0, +0.30.

    Hand-derived: count = 3 (== floor 3, passes); deltas span [-0.30, 0.30] with both edges
    reached and the two interior gaps (0.30 each) <= max step 0.35.
    """
    return [_GridPoint(underlying, tenor, d) for d in (-0.30, 0.0, 0.30)]


def _full_grid(underlying: str = "SPX") -> list[_GridPoint]:
    points: list[_GridPoint] = []
    for tenor in GRID_TENORS:
        points.extend(_full_tenor(underlying, tenor))
    return points


def test_tenor_coverage_floor_passes_when_every_tenor_clears_its_floor() -> None:
    # Hand oracle: each of the 3 pinned tenors has exactly 3 points; floor is 3; 3 >= 3.
    result = check_tenor_coverage_floor(
        _full_grid(), "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.severity == SEVERITY_CRITICAL
    assert result.threshold_version == QC_CONFIG.version
    context = deserialize_context(result.context)
    assert context["breach_count"] == 0
    assert context["breaching_tenors"] == []
    # Worst margin across tenors is 3-3 = 0 (all exactly on floor).
    assert result.measured_value == pytest.approx(0.0)


def test_tenor_coverage_floor_names_the_breaching_tenor() -> None:
    # "1m" gets one point below its floor (2 < 3); "10d"/"3m" stay full. Hand oracle: only
    # "1m" is named, with measured=2 vs floor=3; the passing tenors are not in the list.
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "3m")
    points += [_GridPoint("SPX", "1m", d) for d in (-0.30, 0.30)]  # 2 points
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    breaches = context["breaching_tenors"]
    assert len(breaches) == 1
    assert breaches[0] == {"tenor": "1m", "measured": 2, "floor": 3}
    named = {b["tenor"] for b in breaches}
    assert "10d" not in named and "3m" not in named
    # Worst margin is 2-3 = -1.
    assert result.measured_value == pytest.approx(-1.0)


def test_tenor_coverage_floor_count_exactly_on_floor_passes() -> None:
    # Boundary-exact: a tenor with count == floor passes (the thresholds >= convention).
    points = _full_grid()  # every tenor has exactly 3 == floor 3
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS


def test_tenor_coverage_floor_absent_tenor_is_a_breach() -> None:
    # "3m" has zero points (absent from the grid entirely). Hand oracle: it is a breach,
    # named with measured=0 vs floor=3 — not silently skipped.
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "1m")  # no 3m at all
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    breaches = context["breaching_tenors"]
    assert breaches == [{"tenor": "3m", "measured": 0, "floor": 3}]
    assert result.measured_value == pytest.approx(-3.0)


def test_delta_band_completeness_passes_for_full_band() -> None:
    # Hand oracle: every tenor has [-0.30, 0.0, +0.30] — both edges reached, gaps 0.30 each
    # <= max step 0.35. No gap anywhere -> pass.
    result = check_delta_band_completeness(
        _full_grid(), "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.severity == SEVERITY_CRITICAL
    context = deserialize_context(result.context)
    assert context["gap_count"] == 0
    assert result.measured_value == pytest.approx(0.0)


def test_delta_band_completeness_forces_the_pas2_step() -> None:
    # The prof's ±30Δ pas-2 grid, validated with max_delta_step == band_step == 0.02: a complete
    # band (targets -0.30…-0.02, ATM 0.0, +0.02…+0.30) passes, and dropping ONE interior point
    # opens a 0.04 hole (2·band_step) that FAILS. This is why max_delta_step is tightened to the
    # emission step — a coarser grid (the pre-fix 0.25) let dropped points pass silently.
    pas2_grid = GridQcConfig(
        version="grid-pas2", tenor_floors={t: 3 for t in GRID_TENORS},
        band_low_delta=-0.30, band_high_delta=0.30, band_step=0.02, max_delta_step=0.02,
    )
    pas2_thresholds = QC_CONFIG.model_copy(update={"grid": pas2_grid})
    pas2_targets = (
        [-m / 100.0 for m in range(30, 1, -2)] + [0.0] + [m / 100.0 for m in range(2, 31, 2)]
    )
    complete = [_GridPoint("SPX", t, d) for t in GRID_TENORS for d in pas2_targets]
    ok = check_delta_band_completeness(
        complete, "SPX", GRID_TENORS, thresholds=pas2_thresholds, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert ok.qc_status == STATUS_PASS

    # Drop the -0.16 put from "1m" only -> a 0.04 gap from -0.18 to -0.14, > max step 0.02.
    holed = [p for p in complete if not (p.tenor_label == "1m" and p.target_delta == pytest.approx(-0.16))]
    bad = check_delta_band_completeness(
        holed, "SPX", GRID_TENORS, thresholds=pas2_thresholds, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert bad.qc_status == STATUS_FAIL
    gaps = {g["tenor"]: g for g in deserialize_context(bad.context)["band_gaps"]}
    assert set(gaps) == {"1m"}
    interior = next(m for m in gaps["1m"]["missing"] if m["region"] == "interior_gap")
    assert interior["from_delta"] == pytest.approx(-0.18)
    assert interior["to_delta"] == pytest.approx(-0.14)


def test_delta_band_completeness_flags_interior_gap() -> None:
    # "1m" has a hole: deltas [-0.30, +0.30] only (gap 0.60 > max step 0.35). Hand oracle:
    # "1m" is named with an interior_gap from -0.30 to +0.30; the full tenors pass.
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "3m")
    points += [_GridPoint("SPX", "1m", d) for d in (-0.30, 0.30)]
    result = check_delta_band_completeness(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    gaps = context["band_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["tenor"] == "1m"
    regions = {m["region"] for m in gaps[0]["missing"]}
    assert "interior_gap" in regions
    interior = next(m for m in gaps[0]["missing"] if m["region"] == "interior_gap")
    assert interior["from_delta"] == pytest.approx(-0.30)
    assert interior["to_delta"] == pytest.approx(0.30)


def test_delta_band_completeness_flags_one_sided_chain() -> None:
    # "1m" has only call-side strikes [0.05, 0.20, 0.30] — never reaches the put edge -0.30.
    # Hand oracle: low_edge_unreached for "1m" (the check does NOT let the data redefine the
    # band to its own [0.05, 0.30] span).
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "3m")
    points += [_GridPoint("SPX", "1m", d) for d in (0.05, 0.20, 0.30)]
    result = check_delta_band_completeness(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    gaps = {g["tenor"]: g for g in context["band_gaps"]}
    assert set(gaps) == {"1m"}
    regions = {m["region"] for m in gaps["1m"]["missing"]}
    assert "low_edge_unreached" in regions


def test_delta_band_edge_cases() -> None:
    # The TESTING.md negative-path floor: empty tenor and single-strike tenor are explicit,
    # labelled breaches — not a crash and not a silent pass.
    # "10d" empty (no points), "1m" single strike at ATM, "3m" full.
    points = _full_tenor("SPX", "3m")
    points += [_GridPoint("SPX", "1m", 0.0)]  # single strike
    result = check_delta_band_completeness(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    gaps = {g["tenor"]: g for g in context["band_gaps"]}
    assert set(gaps) == {"10d", "1m"}
    # Empty "10d": too_few_points with point_count 0, and both edges unreached.
    empty_regions = {m["region"] for m in gaps["10d"]["missing"]}
    assert "too_few_points" in empty_regions
    assert {"low_edge_unreached", "high_edge_unreached"} <= empty_regions
    assert gaps["10d"]["point_count"] == 0
    # Single-strike "1m": too_few_points with point_count 1, both edges unreached (ATM only).
    single_regions = {m["region"] for m in gaps["1m"]["missing"]}
    assert "too_few_points" in single_regions
    assert {"low_edge_unreached", "high_edge_unreached"} <= single_regions
    assert gaps["1m"]["point_count"] == 1


def test_grid_thresholds_missing_tenor_floor_raises() -> None:
    # A pinned tenor with no configured floor is a config error — never defaults to zero.
    # The grid config here floors only {"10d", "1m"}, but the pinned grid includes "3m".
    from algotrading.core.config import ConfigFieldError

    partial_grid = GridQcConfig(
        version="grid-qc-partial",
        tenor_floors={"10d": 3, "1m": 3},  # "3m" deliberately missing
        band_low_delta=-0.30,
        band_high_delta=0.30,
        max_delta_step=0.35,
    )
    thresholds = QC_CONFIG.model_copy(update={"grid": partial_grid})
    with pytest.raises(ConfigFieldError) as excinfo:
        check_tenor_coverage_floor(
            _full_grid(), "SPX", GRID_TENORS,
            thresholds=thresholds, run_id=RUN_ID, run_ts=RUN_TS,
        )
    assert excinfo.value.field == "tenor_floors"
    assert excinfo.value.value == "3m"


def test_grid_checks_roll_into_report_and_escalation() -> None:
    # Both new QcResults flow through build_report/escalation_level like the existing checks:
    # a clean grid -> pass report (escalation none); a breaching grid -> fail report that
    # escalates to a page (both checks are critical severity).
    from algotrading.infra.qc import (
        ESCALATION_NONE,
        ESCALATION_PAGE,
        build_report,
        escalation_level,
    )

    clean = [
        check_tenor_coverage_floor(
            _full_grid(), "SPX", GRID_TENORS,
            thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
        ),
        check_delta_band_completeness(
            _full_grid(), "SPX", GRID_TENORS,
            thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
        ),
    ]
    clean_report = build_report(clean, run_id=RUN_ID, run_ts=RUN_TS)
    assert clean_report.overall_status == STATUS_PASS
    assert escalation_level(clean_report) == ESCALATION_NONE

    # A grid missing "3m" entirely fails both checks (coverage absent + band edges unreached).
    thin = _full_tenor("SPX", "10d") + _full_tenor("SPX", "1m")
    breaching = [
        check_tenor_coverage_floor(
            thin, "SPX", GRID_TENORS,
            thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
        ),
        check_delta_band_completeness(
            thin, "SPX", GRID_TENORS,
            thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
        ),
    ]
    breaching_report = build_report(breaching, run_id=RUN_ID, run_ts=RUN_TS)
    assert breaching_report.overall_status == STATUS_FAIL
    assert breaching_report.fail_count == 2
    # Both checks are critical-severity, so a fail escalates to a page (as the existing
    # critical checks do).
    assert escalation_level(breaching_report) == ESCALATION_PAGE
