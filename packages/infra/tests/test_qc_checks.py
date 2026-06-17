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
from algotrading.infra.surfaces import CalendarViolation, SliceFit, SviParams
from fixtures.positions import CALL_100, PUT_100
from fixtures.records import make_record

RUN_ID = "qc-run-2026-06-02"
RUN_TS = datetime(2026, 6, 2, 23, 0, tzinfo=UTC)

QC_CONFIG = QcThresholdConfig(
    version="qc-threshold-1.0.0",
    max_spread_pct=0.05,
    max_quote_age_seconds=30.0,
    min_chain_count=4,
)
THRESHOLDS = QC_CONFIG


@dataclasses.dataclass(frozen=True)
class _FakeSummary:

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
    assert result.check_name == check_name
    assert result.qc_status == status
    assert result.severity == severity
    assert result.threshold_version == QC_CONFIG.version
    assert result.run_id == RUN_ID
    assert result.run_ts == RUN_TS
    return deserialize_context(result.context)


def test_collector_continuity_passes_clean_session() -> None:
    result = check_collector_continuity(
        _summary(gap_count=0), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_collector_continuity_fails_and_names_session() -> None:
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
    assert context["failing_session"] == "sess-bad"
    assert result.target_key == "sess-bad"


def test_collector_continuity_warns_in_gap_band() -> None:
    result = check_collector_continuity(
        _summary(gap_count=3), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_WARN


def test_collector_continuity_fails_on_thin_coverage() -> None:
    result = check_collector_continuity(
        _summary(gap_count=0, subscribed=100, covered=90),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL


def test_collector_continuity_boundary_gap_exact_passes() -> None:
    result = check_collector_continuity(
        _summary(gap_count=5), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_WARN


def test_collector_continuity_empty_subscription_degenerate() -> None:
    result = check_collector_continuity(
        _summary(gap_count=0, subscribed=0, covered=0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert deserialize_context(result.context)["coverage_ratio"] == 1.0


def test_quote_health_passes_tight_quote() -> None:
    batch = SnapshotBatch(
        assessed=(_assessed("AAPL-STK", underlying="AAPL", spread_pct=0.002),), skipped=()
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_quote_health_fails_and_names_quote() -> None:
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
    assert context["failing_quote"] == "MSFT-STK"
    assert result.target_key == "MSFT-STK"


def test_quote_health_boundary_spread_exact_passes() -> None:
    batch = SnapshotBatch(
        assessed=(_assessed("AAPL-STK", underlying="AAPL", spread_pct=0.05),), skipped=()
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_quote_health_ignores_rejected_quotes() -> None:
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


def test_quote_health_fails_when_chain_has_no_two_sided_quotes() -> None:
    batch = SnapshotBatch(
        assessed=(
            _assessed("AAPL-STK", underlying="AAPL", spread_pct=0.002),
            _assessed("AAPL-OPT-1", underlying="AAPL", spread_pct=0.0, status="reject"),
            _assessed("AAPL-OPT-2", underlying="AAPL", spread_pct=0.0, status="reject"),
        ),
        skipped=(),
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    context = _assert_full_shape(
        result,
        check_name="underlying_quote_health",
        status=STATUS_FAIL,
        severity=SEVERITY_CRITICAL,
    )
    assert context["failing_limb"] == "chain_no_two_sided_quotes"
    assert context["option_leg_count"] == 2
    assert context["two_sided_option_count"] == 0


def test_quote_health_passes_when_chain_has_at_least_one_usable_option() -> None:
    batch = SnapshotBatch(
        assessed=(
            _assessed("AAPL-STK", underlying="AAPL", spread_pct=0.002),
            _assessed("AAPL-OPT-1", underlying="AAPL", spread_pct=0.01, status="usable"),
            _assessed("AAPL-OPT-2", underlying="AAPL", spread_pct=0.0, status="reject"),
        ),
        skipped=(),
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_quote_health_treats_a_wide_or_locked_caution_option_as_still_two_sided() -> None:
    batch = SnapshotBatch(
        assessed=(
            _assessed("AAPL-STK", underlying="AAPL", spread_pct=0.002),
            AssessedSnapshot(
                snapshot=_snapshot("AAPL-OPT-1", underlying="AAPL", spread_pct=0.1),
                assessment=QuoteAssessment(status="caution", reasons=("wide_spread",)),
            ),
        ),
        skipped=(),
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_quote_health_treats_a_non_positive_bid_caution_option_as_not_two_sided() -> None:
    batch = SnapshotBatch(
        assessed=(
            _assessed("AAPL-STK", underlying="AAPL", spread_pct=0.002),
            AssessedSnapshot(
                snapshot=_snapshot("AAPL-OPT-1", underlying="AAPL", spread_pct=0.0),
                assessment=QuoteAssessment(
                    status="caution", reasons=("locked", "non_positive_bid")
                ),
            ),
        ),
        skipped=(),
    )
    result = check_underlying_quote_health(
        batch, ["AAPL-STK"], thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL


_EXPECTED_CHAIN = ("AAPL-C90", "AAPL-C95", "AAPL-C100", "AAPL-C105")


def _chain_batch(present_keys: tuple[str, ...]) -> SnapshotBatch:
    return SnapshotBatch(
        assessed=tuple(
            _assessed(key, underlying="AAPL", spread_pct=0.01) for key in present_keys
        ),
        skipped=(),
    )


def test_chain_coverage_passes_full_chain() -> None:
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
    assert context["missing_contracts"] == ["AAPL-C100", "AAPL-C105"]
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


def test_forward_stability_passes_tight_forward() -> None:
    result = check_forward_stability(
        _forward(residual_mad=0.01), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_forward_stability_fails_and_names_maturity() -> None:
    result = check_forward_stability(
        _forward(underlying="SX5E", maturity=0.5, residual_mad=2.0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    context = _assert_full_shape(
        result, check_name="forward_stability", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    assert result.measured_value == pytest.approx(0.02)
    assert context["underlying"] == "SX5E"
    assert context["failing_maturity"] == 0.5
    assert result.target_key == "SX5E@0.5"


def test_forward_stability_fails_on_low_confidence() -> None:
    result = check_forward_stability(
        _forward(residual_mad=0.0, confidence=0.3),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL


def test_forward_stability_boundary_mad_exact_passes() -> None:
    result = check_forward_stability(
        _forward(residual_mad=1.0), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_forward_stability_passes_on_index_scale_forward() -> None:
    result = check_forward_stability(
        _forward(residual_mad=0.159, forward=7400.0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == pytest.approx(0.159 / 7400.0)


def test_forward_stability_fails_a_genuinely_bad_index_forward() -> None:
    result = check_forward_stability(
        _forward(residual_mad=200.0, forward=7400.0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL


def test_parity_residual_passes_small_residuals() -> None:
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
    assert result.measured_value == pytest.approx(0.03)
    assert context["failing_maturity"] == 0.75
    assert context["worst_residual_index"] == 1


def test_parity_residual_passes_on_index_scale_forward() -> None:
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
    result = check_parity_residual(
        _parity_line(()), "AAPL", 0.25, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert deserialize_context(result.context)["worst_residual_index"] == -1


def test_parity_residual_boundary_exact_passes() -> None:
    result = check_parity_residual(
        _parity_line((2.0,)), "AAPL", 0.25, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_iv_convergence_passes_all_converged() -> None:
    results = [_iv_result(f"AAPL-C{k}", STATUS_CONVERGED) for k in (90, 95, 100)]
    result = check_iv_solver_convergence(
        results, "AAPL@0.25", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_iv_convergence_fails_and_names_solver() -> None:
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
    assert failing_keys == {"AAPL-C-DEEP-ITM", "AAPL-C-FAR-OTM"}


def test_iv_convergence_boundary_ratio_exact_passes() -> None:
    results = [_iv_result(f"AAPL-C{k}", STATUS_CONVERGED) for k in range(9)]
    results.append(_iv_result("AAPL-C-BAD", STATUS_ABOVE_MAX))
    result = check_iv_solver_convergence(
        results, "AAPL@0.25", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_iv_convergence_empty_passes() -> None:
    result = check_iv_solver_convergence(
        [], "AAPL@0.25", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_iv_convergence_single_failed_element() -> None:
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


def test_surface_fit_passes_tight_fit() -> None:
    result = check_surface_fit_error(
        _slice_fit(rmse=0.005), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_surface_fit_fails_and_names_maturity() -> None:
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
    assert context["failing_maturity"] == 1.0
    assert result.target_key == "SX5E@1"


def test_surface_fit_boundary_rmse_exact_passes() -> None:
    result = check_surface_fit_error(
        _slice_fit(rmse=0.02), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_surface_fit_fails_arb_violation_despite_tiny_rmse() -> None:
    railed = dataclasses.replace(_slice_fit(rmse=6e-6), arb_free=False)
    result = check_surface_fit_error(railed, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    context = _assert_full_shape(
        result, check_name="surface_fit_error", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    assert context["rmse_ok"] is True
    assert "arb_violation" in context["degeneracy_reasons"]


def test_surface_fit_fails_bound_railed_slice_despite_tiny_rmse() -> None:
    railed = dataclasses.replace(_slice_fit(rmse=6e-6), bound_hits=("rho",))
    result = check_surface_fit_error(railed, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    context = _assert_full_shape(
        result, check_name="surface_fit_error", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    assert "bound_hit:rho" in context["degeneracy_reasons"]


def test_surface_fit_passes_benign_a_floor_when_minimum_variance_is_positive() -> None:
    svi = SviParams(a=1e-30, b=0.02, rho=-0.4, m=0.0, sigma=0.08)
    assert svi.minimum_total_variance() > 0.0
    benign = dataclasses.replace(
        _slice_fit(rmse=6e-6), svi=svi, bound_hits=("a_lower",)
    )
    result = check_surface_fit_error(benign, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    context = _assert_full_shape(
        result, check_name="surface_fit_error", status=STATUS_PASS, severity=SEVERITY_WARNING
    )
    assert context["degeneracy_reasons"] == []
    assert context["benign_bound_hits"] == ["a_lower"]
    assert context["bound_hits"] == ["a_lower"]


def test_surface_fit_still_fails_a_floor_with_a_genuine_rho_rail() -> None:
    svi = SviParams(a=1e-30, b=0.02, rho=-0.4, m=0.0, sigma=0.08)
    railed = dataclasses.replace(
        _slice_fit(rmse=6e-6), svi=svi, bound_hits=("a_lower", "rho_lower")
    )
    result = check_surface_fit_error(railed, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    context = _assert_full_shape(
        result, check_name="surface_fit_error", status=STATUS_FAIL, severity=SEVERITY_WARNING
    )
    assert "bound_hit:rho_lower" in context["degeneracy_reasons"]
    assert context["benign_bound_hits"] == ["a_lower"]


def test_surface_fit_non_svi_converged_none_is_not_penalised() -> None:
    clean = dataclasses.replace(_slice_fit(rmse=0.005), converged=None, method="nonparametric")
    result = check_surface_fit_error(clean, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    context = _assert_full_shape(
        result, check_name="surface_fit_error", status=STATUS_PASS, severity=SEVERITY_WARNING
    )
    assert context["degeneracy_reasons"] == []


def test_calendar_sanity_passes_no_violations() -> None:
    result = check_calendar_sanity(
        [], "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_calendar_sanity_fails_and_names_maturity_pair() -> None:
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
    assert context["failing_maturity_short"] == 0.5
    assert context["failing_maturity_long"] == 1.0
    assert context["failing_k"] == pytest.approx(0.1)


def test_calendar_sanity_short_end_noise_is_a_warning_not_critical() -> None:
    # The 2026-06-16 SX5E wiggle: w_short 1.87e-3 vs w_long 1.62e-3 — a ~2.5e-4 total-variance
    # inversion at the short end. Below the 5e-4 absolute tolerance AND inside the ultra-short
    # maturity floor → a WARNING, never a page (ADR 0052).
    wiggle = CalendarViolation(
        k=0.0, maturity_short=10.0 / 365.0, maturity_long=1.0 / 12.0,
        w_short=1.87e-3, w_long=1.62e-3,
    )
    result = check_calendar_sanity(
        [wiggle], "SX5E", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_WARN
    assert result.severity == SEVERITY_WARNING
    context = deserialize_context(result.context)
    assert context["material_count"] == 0
    assert context["noise_count"] == 1


def test_calendar_sanity_material_inversion_is_critical() -> None:
    # A gross inversion well inside the liquid range: w_short 0.05 vs w_long 0.02 at 6m vs 12m —
    # a 0.03 total-variance gap, far above both the absolute (5e-4) and relative (5% of 0.02 =
    # 1e-3) tolerances, at non-ultra-short maturities → CRITICAL (ADR 0052).
    gross = CalendarViolation(
        k=0.1, maturity_short=0.5, maturity_long=1.0, w_short=0.05, w_long=0.02,
    )
    result = check_calendar_sanity(
        [gross], "SX5E", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    assert result.severity == SEVERITY_CRITICAL
    context = deserialize_context(result.context)
    assert context["material_count"] == 1
    assert result.measured_value == pytest.approx(1.0)


def test_calendar_sanity_relative_tolerance_spares_a_proportionally_small_gap() -> None:
    # An inversion above the absolute floor but small relative to the long-leg variance: a 6e-4
    # gap on a w_long of 0.10 is 0.6% — under the 5% relative tolerance → WARNING, not CRITICAL.
    small_rel = CalendarViolation(
        k=0.0, maturity_short=0.5, maturity_long=1.0, w_short=0.1006, w_long=0.10,
    )
    result = check_calendar_sanity(
        [small_rel], "SX5E", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_WARN
    assert result.severity == SEVERITY_WARNING


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


def test_greek_sanity_passes_clean_line() -> None:
    result = check_greek_sanity(
        _position(CALL_100), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS
    assert result.measured_value == 0.0


def test_greek_sanity_fails_negative_gamma_and_names_contract() -> None:
    clean = _position(CALL_100)
    bad = dataclasses.replace(clean.greeks, gamma=-0.01)
    result = check_greek_sanity(
        _position(CALL_100, greeks=bad), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    context = _assert_full_shape(
        result, check_name="greek_sanity", status=STATUS_FAIL, severity=SEVERITY_CRITICAL
    )
    assert result.measured_value == 1.0
    assert context["failing_contract"] == CALL_100.contract_key
    reasons = {breach["reason"] for breach in context["breaches"]}
    assert "negative_gamma" in reasons


def test_greek_sanity_fails_call_delta_out_of_range() -> None:
    clean = _position(CALL_100)
    bad = dataclasses.replace(clean.greeks, delta=1.5)
    result = check_greek_sanity(
        _position(CALL_100, greeks=bad), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    reasons = {breach["reason"] for breach in deserialize_context(result.context)["breaches"]}
    assert "call_delta_out_of_range" in reasons


def test_greek_sanity_fails_put_delta_out_of_range() -> None:
    clean = _position(PUT_100)
    bad = dataclasses.replace(clean.greeks, delta=0.5)
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
    line = _position(CALL_100)
    broker = BrokerGreeks(contract_key="WRONG-CONTRACT", delta=0.0)
    with pytest.raises(ContractKeyMismatchError) as excinfo:
        check_greek_sanity(line, broker=broker, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    assert excinfo.value.line_key == CALL_100.contract_key
    assert excinfo.value.broker_key == "WRONG-CONTRACT"


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
    result = check_scenario_completeness(
        (), (), "PORT-1", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_PASS


def test_anomaly_flags_injected_spike() -> None:
    baseline = [50.0, 51.0, 49.0, 50.5, 49.5, 50.0, 51.0, 49.0]
    result = detect_anomaly(
        500.0, baseline, "event_rate", "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert result.qc_status == STATUS_FAIL
    context = deserialize_context(result.context)
    assert context["metric"] == "event_rate"
    assert context["target"] == "AAPL"
    assert result.measured_value > THRESHOLDS.anomaly.mad_multiplier


def test_anomaly_does_not_flag_value_within_baseline() -> None:
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
    assert robust_z_score(5.0, [5.0, 5.0, 5.0]) == 0.0
    assert math.isinf(robust_z_score(6.0, [5.0, 5.0, 5.0]))


def test_anomaly_single_element_baseline() -> None:
    same = detect_anomaly(
        7.0, [7.0], "stale_ratio", "MSFT", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert same.qc_status == STATUS_PASS
    far = detect_anomaly(
        99.0, [7.0], "stale_ratio", "MSFT", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )
    assert far.qc_status == STATUS_FAIL


def test_robust_z_score_matches_hand_computed_mad() -> None:
    baseline = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0]
    assert robust_z_score(40.0, baseline) == pytest.approx(24.5 / (1.4826 * 3.0), rel=1e-9)


def test_supplementary_thresholds_flow_from_config_into_a_check_verdict() -> None:
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
    assert THRESHOLDS.continuity.max_gap_count == 5
    assert THRESHOLDS.continuity.warn_gap_count == 1
    assert THRESHOLDS.continuity.min_coverage_ratio == pytest.approx(0.95)
    assert THRESHOLDS.forward_engine.max_rel_residual_mad == pytest.approx(0.01)
    assert THRESHOLDS.forward_engine.min_forward_confidence == pytest.approx(0.5)
    assert THRESHOLDS.forward_engine.max_rel_parity_residual == pytest.approx(0.02)
    assert THRESHOLDS.fit_tolerance.max_non_convergence_ratio == pytest.approx(0.10)
    assert THRESHOLDS.fit_tolerance.max_surface_rmse == pytest.approx(0.02)
    assert THRESHOLDS.anomaly.mad_multiplier == pytest.approx(5.0)


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

    underlying: str
    tenor_label: str
    target_delta: float
    delta: float = 0.0


def _full_tenor(underlying: str, tenor: str) -> list[_GridPoint]:
    return [_GridPoint(underlying, tenor, d) for d in (-0.30, 0.0, 0.30)]


def _full_grid(underlying: str = "SPX") -> list[_GridPoint]:
    points: list[_GridPoint] = []
    for tenor in GRID_TENORS:
        points.extend(_full_tenor(underlying, tenor))
    return points


def test_tenor_coverage_floor_passes_when_every_tenor_clears_its_floor() -> None:
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
    assert result.measured_value == pytest.approx(0.0)


def test_tenor_coverage_floor_partial_interior_is_a_critical_breach() -> None:
    # 10d and 3m are liquid (clear floor) so [10d, 3m] is the liquid range; 1m sits strictly
    # inside it with 2/3 points — a partial-capture collapse of a maturity that should be
    # liquid, which is the within-liquid-range CRITICAL tooth (ADR 0052).
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "3m")
    points += [_GridPoint("SPX", "1m", d) for d in (-0.30, 0.30)]
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    assert result.severity == SEVERITY_CRITICAL
    context = deserialize_context(result.context)
    breaches = context["breaching_tenors"]
    assert len(breaches) == 1
    assert {k: breaches[0][k] for k in ("tenor", "measured", "floor")} == {
        "tenor": "1m", "measured": 2, "floor": 3
    }
    named = {b["tenor"] for b in breaches}
    assert "10d" not in named and "3m" not in named
    assert result.measured_value == pytest.approx(-1.0)


def test_tenor_coverage_floor_interior_zero_is_interpolated_not_a_breach() -> None:
    # 10d and 3m are liquid; 1m carries NO points but is bracketed by two liquid neighbours, so
    # it is filled by Eq.-22 total-variance interpolation — covered, no breach (ADR 0052).
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "3m")
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS
    context = deserialize_context(result.context)
    assert context["breach_count"] == 0
    assert context["edge_warning_count"] == 0
    # 1m counts as a covered monitored maturity.
    assert context["monitored_tenor_count"] == 3
    assert context["monitored_covered_count"] == 3
    assert context["coverage_ratio"] == pytest.approx(1.0)


def test_tenor_coverage_floor_count_exactly_on_floor_passes() -> None:
    points = _full_grid()
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_PASS


def test_tenor_coverage_floor_edge_tenor_is_warning_not_critical() -> None:
    # 10d and 1m are liquid (span [10d, 1m]); 3m sits ABOVE the liquid range — an extrapolation
    # edge. Its emptiness is a labelled fallback (WARNING), not a CRITICAL (ADR 0052).
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "1m")
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_WARN
    assert result.severity == SEVERITY_WARNING
    context = deserialize_context(result.context)
    assert context["breach_count"] == 0
    edges = context["edge_tenors"]
    assert len(edges) == 1
    assert edges[0]["tenor"] == "3m"
    assert edges[0]["provenance"] == "extrapolated"
    assert result.measured_value == pytest.approx(-3.0)


def test_tenor_coverage_floor_collapsed_core_pages_critical_via_ratio() -> None:
    # A genuine liquid-core collapse: only 10d is liquid (span [10d, 10d]); 1m carries a partial
    # 2/3 capture inside... but with the span pinned to a single point, 1m and 3m are edges. To
    # exercise the ratio tooth we keep a real interior range: 10d and 3m liquid, and 1m a
    # PARTIAL interior collapse — coverage_ratio = 2/3 < 0.95 → CRITICAL.
    points = _full_tenor("SPX", "10d") + _full_tenor("SPX", "3m")
    points += [_GridPoint("SPX", "1m", -0.30)]  # 1/3, interior, partial -> core breach
    result = check_tenor_coverage_floor(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_FAIL
    assert result.severity == SEVERITY_CRITICAL
    context = deserialize_context(result.context)
    assert context["coverage_ratio"] == pytest.approx(2.0 / 3.0)
    assert context["coverage_ratio"] < context["min_coverage_ratio"]


def test_delta_band_completeness_passes_for_full_band() -> None:
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
    # Only 3m carries a complete band, so the liquid range is [3m, 3m]; 10d and 1m sit below it
    # (extrapolation edges). Their partial/empty bands are a labelled fallback (WARNING), not a
    # CRITICAL (ADR 0052).
    points = _full_tenor("SPX", "3m")
    points += [_GridPoint("SPX", "1m", 0.0)]
    result = check_delta_band_completeness(
        points, "SPX", GRID_TENORS,
        thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
    )
    assert result.qc_status == STATUS_WARN
    assert result.severity == SEVERITY_WARNING
    context = deserialize_context(result.context)
    assert context["gap_count"] == 0  # no CRITICAL liquid-core gaps
    gaps = {g["tenor"]: g for g in context["edge_band_gaps"]}
    assert set(gaps) == {"10d", "1m"}
    empty_regions = {m["region"] for m in gaps["10d"]["missing"]}
    assert "too_few_points" in empty_regions
    assert {"low_edge_unreached", "high_edge_unreached"} <= empty_regions
    assert gaps["10d"]["point_count"] == 0
    assert gaps["10d"]["provenance"] == "extrapolated"
    single_regions = {m["region"] for m in gaps["1m"]["missing"]}
    assert "too_few_points" in single_regions
    assert {"low_edge_unreached", "high_edge_unreached"} <= single_regions
    assert gaps["1m"]["point_count"] == 1


def test_grid_thresholds_missing_tenor_floor_raises() -> None:
    from algotrading.core.config import ConfigFieldError

    partial_grid = GridQcConfig(
        version="grid-qc-partial",
        tenor_floors={"10d": 3, "1m": 3},
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
    from algotrading.infra.qc import (
        ESCALATION_NONE,
        ESCALATION_NOTICE,
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

    # A genuine liquid-core collapse (10d and 3m liquid, 1m a partial interior capture) still
    # pages CRITICAL through both grid checks.
    collapsed = _full_tenor("SPX", "10d") + _full_tenor("SPX", "3m")
    collapsed += [_GridPoint("SPX", "1m", -0.30)]
    breaching = [
        check_tenor_coverage_floor(
            collapsed, "SPX", GRID_TENORS,
            thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
        ),
        check_delta_band_completeness(
            collapsed, "SPX", GRID_TENORS,
            thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
        ),
    ]
    breaching_report = build_report(breaching, run_id=RUN_ID, run_ts=RUN_TS)
    assert breaching_report.overall_status == STATUS_FAIL
    assert breaching_report.fail_count == 2
    assert escalation_level(breaching_report) == ESCALATION_PAGE

    # An edge-only illiquidity (3m absent, beyond the liquid [10d, 1m] range) degrades to a
    # NOTICE warning, never a page.
    edge_only = _full_tenor("SPX", "10d") + _full_tenor("SPX", "1m")
    warned = [
        check_tenor_coverage_floor(
            edge_only, "SPX", GRID_TENORS,
            thresholds=GRID_THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS,
        ),
    ]
    warned_report = build_report(warned, run_id=RUN_ID, run_ts=RUN_TS)
    assert warned_report.overall_status == STATUS_WARN
    assert escalation_level(warned_report) == ESCALATION_NOTICE
