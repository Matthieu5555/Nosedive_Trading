from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from statistics import median

from algotrading.core.config import QcThresholdConfig
from algotrading.core.log import get_logger
from algotrading.infra.contracts import QcResult
from algotrading.infra.forwards import ForwardEstimate, ParityLine
from algotrading.infra.iv import STATUS_CONVERGED, IvResult
from algotrading.infra.risk import BrokerGreeks, GreekDiscrepancy, PositionRisk, reconcile
from algotrading.infra.snapshots import SnapshotBatch
from algotrading.infra.surfaces import CalendarViolation, SliceFit
from algotrading.infra.utils import robust_zscore_vs_baseline

from .errors import ContractKeyMismatchError, EmptyBaselineError
from .inputs import CollectorContinuityInput, GridPointInput, IvSpreadInput
from .result import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    build_result,
)

_log = get_logger(__name__)

CHECK_COLLECTOR_CONTINUITY = "collector_continuity"
CHECK_UNDERLYING_QUOTE_HEALTH = "underlying_quote_health"
CHECK_OPTION_CHAIN_COVERAGE = "option_chain_coverage"
CHECK_FORWARD_STABILITY = "forward_stability"
CHECK_PARITY_RESIDUAL = "parity_residual"
CHECK_IV_SOLVER_CONVERGENCE = "iv_solver_convergence"
CHECK_SURFACE_FIT_ERROR = "surface_fit_error"
CHECK_CALENDAR_SANITY = "calendar_sanity"
CHECK_GREEK_SANITY = "greek_sanity"
CHECK_SCENARIO_COMPLETENESS = "scenario_completeness"
CHECK_TENOR_COVERAGE_FLOOR = "tenor_coverage_floor"
CHECK_DELTA_BAND_COMPLETENESS = "delta_band_completeness"
CHECK_ANOMALY = "anomaly_detection"
CHECK_PUT_CALL_IV_SPREAD = "put_call_iv_spread"

CHECK_NAMES: tuple[str, ...] = (
    CHECK_COLLECTOR_CONTINUITY,
    CHECK_UNDERLYING_QUOTE_HEALTH,
    CHECK_OPTION_CHAIN_COVERAGE,
    CHECK_FORWARD_STABILITY,
    CHECK_PARITY_RESIDUAL,
    CHECK_IV_SOLVER_CONVERGENCE,
    CHECK_SURFACE_FIT_ERROR,
    CHECK_CALENDAR_SANITY,
    CHECK_GREEK_SANITY,
    CHECK_SCENARIO_COMPLETENESS,
    CHECK_TENOR_COVERAGE_FLOOR,
    CHECK_DELTA_BAND_COMPLETENESS,
    CHECK_PUT_CALL_IV_SPREAD,
)

_USABLE_QUOTE_STATUS = "usable"

_NO_TWO_SIDED_REASONS = frozenset({"non_positive_bid", "crossed"})


def check_collector_continuity(
    summary: CollectorContinuityInput,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    gap_count = summary.gap_count
    subscribed = summary.subscribed_count
    covered = summary.covered_count
    coverage_ratio = covered / subscribed if subscribed > 0 else 1.0
    context = {
        "failing_session": summary.session_id,
        "gap_count": gap_count,
        "coverage_ratio": coverage_ratio,
        "subscribed_count": subscribed,
        "covered_count": covered,
    }
    if (
        gap_count > thresholds.continuity.max_gap_count
        or coverage_ratio < thresholds.continuity.min_coverage_ratio
    ):
        status = STATUS_FAIL
    elif gap_count > thresholds.continuity.warn_gap_count:
        status = STATUS_WARN
    else:
        status = STATUS_PASS
    if status == STATUS_FAIL:
        _log.warning(
            "qc collector_continuity fail: session=%s gaps=%d coverage=%.3f",
            summary.session_id,
            gap_count,
            coverage_ratio,
            extra={
                "session_id": summary.session_id,
                "gap_count": gap_count,
                "coverage_ratio": coverage_ratio,
            },
        )
    return build_result(
        check_name=CHECK_COLLECTOR_CONTINUITY,
        target_key=summary.session_id,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=float(gap_count),
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_underlying_quote_health(
    batch: SnapshotBatch,
    underlying_instrument_keys: Sequence[str],
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    anchors = set(underlying_instrument_keys)
    worst_key = ""
    worst_spread = 0.0
    seen = 0
    option_seen = 0
    two_sided_option_count = 0
    for assessed in batch.assessed:
        snap = assessed.snapshot
        if snap.instrument_key in anchors:
            if assessed.assessment.status != _USABLE_QUOTE_STATUS:
                continue
            seen += 1
            if snap.spread_pct > worst_spread:
                worst_spread = snap.spread_pct
                worst_key = snap.instrument_key
            continue
        option_seen += 1
        reasons = set(assessed.assessment.reasons)
        is_two_sided = (
            assessed.assessment.status != "reject" and not (reasons & _NO_TWO_SIDED_REASONS)
        )
        if is_two_sided:
            two_sided_option_count += 1
    chain_has_no_two_sided = option_seen > 0 and two_sided_option_count == 0
    spread_breach = worst_spread > thresholds.max_spread_pct
    status = STATUS_FAIL if (spread_breach or chain_has_no_two_sided) else STATUS_PASS
    if chain_has_no_two_sided and not spread_breach:
        failing_limb = "chain_no_two_sided_quotes"
        target = sorted(anchors)[0] if anchors else ""
    elif spread_breach:
        failing_limb = "anchor_spread"
        target = worst_key
    else:
        failing_limb = ""
        target = worst_key if worst_key else (sorted(anchors)[0] if anchors else "")
    context = {
        "failing_quote": worst_key,
        "failing_limb": failing_limb,
        "worst_spread_pct": worst_spread,
        "max_spread_pct": thresholds.max_spread_pct,
        "usable_quote_count": seen,
        "option_leg_count": option_seen,
        "two_sided_option_count": two_sided_option_count,
    }
    return build_result(
        check_name=CHECK_UNDERLYING_QUOTE_HEALTH,
        target_key=target,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=worst_spread,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_option_chain_coverage(
    batch: SnapshotBatch,
    underlying: str,
    expected_contract_keys: Sequence[str],
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    expected = set(expected_contract_keys)
    usable_keys = {
        assessed.snapshot.instrument_key
        for assessed in batch.assessed
        if assessed.snapshot.underlying == underlying
        and assessed.assessment.status == _USABLE_QUOTE_STATUS
    }
    present = expected & usable_keys
    missing = sorted(expected - usable_keys)
    usable_count = len(present)
    status = STATUS_PASS if usable_count >= thresholds.min_chain_count else STATUS_FAIL
    context = {
        "underlying": underlying,
        "usable_count": usable_count,
        "expected_count": len(expected),
        "min_chain_count": thresholds.min_chain_count,
        "missing_contracts": missing,
    }
    return build_result(
        check_name=CHECK_OPTION_CHAIN_COVERAGE,
        target_key=underlying,
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=float(usable_count),
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_forward_stability(
    estimate: ForwardEstimate,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    target_key = f"{estimate.underlying}@{estimate.maturity_years:g}"
    forward = estimate.forward
    relative_residual_mad = (
        estimate.residual_mad / forward
        if forward is not None and forward > 0.0
        else math.inf
    )
    unstable = (
        relative_residual_mad > thresholds.forward_engine.max_rel_residual_mad
        or estimate.confidence < thresholds.forward_engine.min_forward_confidence
    )
    status = STATUS_FAIL if unstable else STATUS_PASS
    context = {
        "underlying": estimate.underlying,
        "failing_maturity": estimate.maturity_years,
        "relative_residual_mad": relative_residual_mad,
        "residual_mad": estimate.residual_mad,
        "forward": forward,
        "confidence": estimate.confidence,
        "quality_label": estimate.quality_label,
        "reason_code": estimate.reason_code,
        "max_rel_residual_mad": thresholds.forward_engine.max_rel_residual_mad,
        "min_confidence": thresholds.forward_engine.min_forward_confidence,
    }
    return build_result(
        check_name=CHECK_FORWARD_STABILITY,
        target_key=target_key,
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=relative_residual_mad,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_parity_residual(
    line: ParityLine,
    underlying: str,
    maturity_years: float,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    residuals = line.residuals
    worst_index = -1
    worst_abs = 0.0
    for index, residual in enumerate(residuals):
        magnitude = abs(residual)
        if magnitude > worst_abs:
            worst_abs = magnitude
            worst_index = index
    forward = line.forward
    worst_relative = worst_abs / forward if forward > 0.0 else math.inf
    max_rel_parity_residual = thresholds.forward_engine.max_rel_parity_residual
    status = STATUS_PASS if worst_relative <= max_rel_parity_residual else STATUS_FAIL
    context = {
        "underlying": underlying,
        "failing_maturity": maturity_years,
        "worst_relative_residual": worst_relative,
        "worst_residual": worst_abs,
        "forward": forward,
        "worst_residual_index": worst_index,
        "residual_count": len(residuals),
        "max_rel_parity_residual": thresholds.forward_engine.max_rel_parity_residual,
    }
    return build_result(
        check_name=CHECK_PARITY_RESIDUAL,
        target_key=f"{underlying}@{maturity_years:g}",
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=worst_relative,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_iv_solver_convergence(
    results: Sequence[IvResult],
    target_key: str,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    total = len(results)
    failed = [r for r in results if r.status != STATUS_CONVERGED]
    ratio = len(failed) / total if total > 0 else 0.0
    max_non_convergence_ratio = thresholds.fit_tolerance.max_non_convergence_ratio
    status = STATUS_PASS if ratio <= max_non_convergence_ratio else STATUS_FAIL
    failing_solvers = [
        {"contract_key": r.contract_key, "status": r.status} for r in failed
    ]
    context = {
        "target": target_key,
        "non_convergence_ratio": ratio,
        "failed_count": len(failed),
        "total_count": total,
        "max_non_convergence_ratio": thresholds.fit_tolerance.max_non_convergence_ratio,
        "failing_solvers": failing_solvers,
    }
    return build_result(
        check_name=CHECK_IV_SOLVER_CONVERGENCE,
        target_key=target_key,
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=ratio,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_surface_fit_error(
    fit: SliceFit,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    rmse_ok = fit.rmse <= thresholds.fit_tolerance.max_surface_rmse
    degeneracy_reasons: list[str] = []
    if not fit.arb_free:
        degeneracy_reasons.append("arb_violation")
    if fit.bound_hits:
        degeneracy_reasons.append(f"bound_hit:{','.join(fit.bound_hits)}")
    if fit.converged is False:
        degeneracy_reasons.append("not_converged")
    status = STATUS_PASS if (rmse_ok and not degeneracy_reasons) else STATUS_FAIL
    context = {
        "underlying": fit.underlying,
        "failing_maturity": fit.maturity_years,
        "rmse": fit.rmse,
        "rmse_ok": rmse_ok,
        "method": fit.method,
        "n_points": fit.n_points,
        "max_surface_rmse": thresholds.fit_tolerance.max_surface_rmse,
        "arb_free": fit.arb_free,
        "bound_hits": list(fit.bound_hits),
        "converged": fit.converged,
        "degeneracy_reasons": degeneracy_reasons,
    }
    return build_result(
        check_name=CHECK_SURFACE_FIT_ERROR,
        target_key=f"{fit.underlying}@{fit.maturity_years:g}",
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=fit.rmse,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_calendar_sanity(
    violations: Sequence[CalendarViolation],
    underlying: str,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    count = len(violations)
    status = STATUS_PASS if count == 0 else STATUS_FAIL
    worst = None
    worst_gap = 0.0
    for violation in violations:
        gap = violation.w_short - violation.w_long
        if worst is None or gap > worst_gap:
            worst = violation
            worst_gap = gap
    context: dict[str, object] = {
        "underlying": underlying,
        "violation_count": count,
    }
    if worst is not None:
        context.update(
            {
                "failing_maturity_short": worst.maturity_short,
                "failing_maturity_long": worst.maturity_long,
                "failing_k": worst.k,
                "w_short": worst.w_short,
                "w_long": worst.w_long,
            }
        )
    return build_result(
        check_name=CHECK_CALENDAR_SANITY,
        target_key=underlying,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=float(count),
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_greek_sanity(
    line: PositionRisk,
    *,
    broker: BrokerGreeks | None = None,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    contract_key = line.valuation.contract_key
    greeks = line.greeks
    right = line.valuation.option_right.lower()
    breaches: list[dict[str, object]] = []

    sign_problems: list[tuple[str, float, str]] = []
    for name, value in (
        ("price", greeks.price),
        ("delta", greeks.delta),
        ("gamma", greeks.gamma),
        ("vega", greeks.vega),
        ("theta", greeks.theta),
        ("rho", greeks.rho),
    ):
        if not math.isfinite(value):
            sign_problems.append((name, value, "non_finite"))
    if math.isfinite(greeks.gamma) and greeks.gamma < 0.0:
        sign_problems.append(("gamma", greeks.gamma, "negative_gamma"))
    if math.isfinite(greeks.vega) and greeks.vega < 0.0:
        sign_problems.append(("vega", greeks.vega, "negative_vega"))
    if math.isfinite(greeks.delta):
        if right.startswith("c") and not (0.0 <= greeks.delta <= 1.0):
            sign_problems.append(("delta", greeks.delta, "call_delta_out_of_range"))
        elif right.startswith("p") and not (-1.0 <= greeks.delta <= 0.0):
            sign_problems.append(("delta", greeks.delta, "put_delta_out_of_range"))
    for greek, value, reason in sign_problems:
        breaches.append({"greek": greek, "value": value, "reason": reason})

    recon_breaches: list[GreekDiscrepancy] = []
    if broker is not None:
        if broker.contract_key != contract_key:
            raise ContractKeyMismatchError(contract_key, broker.contract_key)
        recon_breaches = reconcile(line, broker)
        for discrepancy in recon_breaches:
            breaches.append(
                {
                    "greek": discrepancy.greek,
                    "computed": discrepancy.computed,
                    "broker": discrepancy.broker,
                    "abs_diff": discrepancy.abs_diff,
                    "threshold": discrepancy.threshold,
                    "reason": "broker_reconcile_breach",
                }
            )

    status = STATUS_PASS if not breaches else STATUS_FAIL
    context = {
        "failing_contract": contract_key,
        "option_right": line.valuation.option_right,
        "breach_count": len(breaches),
        "breaches": breaches,
    }
    return build_result(
        check_name=CHECK_GREEK_SANITY,
        target_key=contract_key,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=float(len(breaches)),
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_scenario_completeness(
    produced_cells: Sequence[tuple[str, str]],
    expected_cells: Sequence[tuple[str, str]],
    portfolio_id: str,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    produced = set(produced_cells)
    expected = set(expected_cells)
    missing = sorted(expected - produced)
    status = STATUS_PASS if not missing else STATUS_FAIL
    context = {
        "portfolio_id": portfolio_id,
        "expected_count": len(expected),
        "produced_count": len(produced & expected),
        "missing_count": len(missing),
        "missing_cells": [
            {"scenario_id": scenario_id, "contract_key": contract_key}
            for scenario_id, contract_key in missing
        ],
    }
    return build_result(
        check_name=CHECK_SCENARIO_COMPLETENESS,
        target_key=portfolio_id,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=float(len(missing)),
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_tenor_coverage_floor(
    points: Sequence[GridPointInput],
    underlying: str,
    tenor_grid: Sequence[str],
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    counts: dict[str, int] = {tenor: 0 for tenor in tenor_grid}
    for point in points:
        if point.tenor_label in counts:
            counts[point.tenor_label] += 1
    breaches: list[dict[str, object]] = []
    worst_margin: float | None = None
    for tenor in tenor_grid:
        floor = thresholds.grid.floor_for(tenor)
        count = counts[tenor]
        margin = float(count - floor)
        if worst_margin is None or margin < worst_margin:
            worst_margin = margin
        if count < floor:
            breaches.append({"tenor": tenor, "measured": count, "floor": floor})
    status = STATUS_PASS if not breaches else STATUS_FAIL
    measured = worst_margin if worst_margin is not None else 0.0
    context = {
        "underlying": underlying,
        "pinned_tenor_count": len(tenor_grid),
        "breach_count": len(breaches),
        "breaching_tenors": breaches,
    }
    if status == STATUS_FAIL:
        _log.warning(
            "qc tenor_coverage_floor fail: underlying=%s breaches=%d",
            underlying,
            len(breaches),
            extra={"underlying": underlying, "breaching_tenors": breaches},
        )
    return build_result(
        check_name=CHECK_TENOR_COVERAGE_FLOOR,
        target_key=underlying,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=measured,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_delta_band_completeness(
    points: Sequence[GridPointInput],
    underlying: str,
    tenor_grid: Sequence[str],
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    by_tenor: dict[str, list[float]] = {tenor: [] for tenor in tenor_grid}
    for point in points:
        if point.tenor_label in by_tenor:
            by_tenor[point.tenor_label].append(point.target_delta)
    band_low = thresholds.grid.band_low_delta
    band_high = thresholds.grid.band_high_delta
    max_step = thresholds.grid.max_delta_step
    edge_tol = 1e-9
    gaps: list[dict[str, object]] = []
    for tenor in tenor_grid:
        deltas = sorted(by_tenor[tenor])
        reasons = _band_gap_reasons(
            deltas,
            band_low=band_low,
            band_high=band_high,
            max_step=max_step,
            edge_tol=edge_tol,
        )
        if reasons:
            gaps.append({"tenor": tenor, "point_count": len(deltas), "missing": reasons})
    status = STATUS_PASS if not gaps else STATUS_FAIL
    context = {
        "underlying": underlying,
        "band_low_delta": band_low,
        "band_high_delta": band_high,
        "max_delta_step": max_step,
        "pinned_tenor_count": len(tenor_grid),
        "gap_count": len(gaps),
        "band_gaps": gaps,
    }
    if status == STATUS_FAIL:
        _log.warning(
            "qc delta_band_completeness fail: underlying=%s gaps=%d",
            underlying,
            len(gaps),
            extra={"underlying": underlying, "band_gaps": gaps},
        )
    return build_result(
        check_name=CHECK_DELTA_BAND_COMPLETENESS,
        target_key=underlying,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=float(len(gaps)),
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_put_call_iv_spread(
    spreads: Sequence[IvSpreadInput],
    underlying: str,
    *,
    max_abs_spread: float,
    threshold_version: str,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    breaches = [
        {
            "tenor": point.tenor_label,
            "delta_band": point.delta_band,
            "iv_spread": point.iv_spread,
        }
        for point in spreads
        if abs(point.iv_spread) > max_abs_spread
    ]
    status = STATUS_PASS if not breaches else STATUS_FAIL
    context = {
        "underlying": underlying,
        "max_abs_spread": max_abs_spread,
        "point_count": len(spreads),
        "breach_count": len(breaches),
        "breaches": breaches,
    }
    if status == STATUS_FAIL:
        _log.warning(
            "qc put_call_iv_spread fail: underlying=%s breaches=%d max_abs_spread=%g",
            underlying,
            len(breaches),
            max_abs_spread,
            extra={"underlying": underlying, "breaches": breaches},
        )
    return build_result(
        check_name=CHECK_PUT_CALL_IV_SPREAD,
        target_key=underlying,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=float(len(breaches)),
        threshold_version=threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def _band_gap_reasons(
    deltas: Sequence[float],
    *,
    band_low: float,
    band_high: float,
    max_step: float,
    edge_tol: float,
) -> list[dict[str, object]]:
    reasons: list[dict[str, object]] = []
    if len(deltas) < 2:
        reasons.append({"region": "too_few_points", "point_count": len(deltas)})
        if not deltas or deltas[0] > band_low + edge_tol:
            reasons.append({"region": "low_edge_unreached", "band_low": band_low})
        if not deltas or deltas[-1] < band_high - edge_tol:
            reasons.append({"region": "high_edge_unreached", "band_high": band_high})
        return reasons
    if deltas[0] > band_low + edge_tol:
        reasons.append(
            {"region": "low_edge_unreached", "band_low": band_low, "nearest": deltas[0]}
        )
    if deltas[-1] < band_high - edge_tol:
        reasons.append(
            {"region": "high_edge_unreached", "band_high": band_high, "nearest": deltas[-1]}
        )
    for lo, hi in zip(deltas, deltas[1:], strict=False):
        if hi - lo > max_step + edge_tol:
            reasons.append({"region": "interior_gap", "from_delta": lo, "to_delta": hi})
    return reasons


def robust_z_score(observed: float, baseline: Sequence[float]) -> float:
    if not baseline:
        raise EmptyBaselineError(observed)
    return abs(robust_zscore_vs_baseline(observed, baseline))


def detect_anomaly(
    observed: float,
    baseline: Sequence[float],
    metric_name: str,
    target_key: str,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    score = robust_z_score(observed, baseline)
    status = STATUS_FAIL if score > thresholds.anomaly.mad_multiplier else STATUS_PASS
    context = {
        "metric": metric_name,
        "target": target_key,
        "observed": observed,
        "baseline_median": median(baseline),
        "robust_z_score": score if math.isfinite(score) else "inf",
        "baseline_size": len(baseline),
        "mad_multiplier": thresholds.anomaly.mad_multiplier,
    }
    return build_result(
        check_name=CHECK_ANOMALY,
        target_key=target_key,
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=score if math.isfinite(score) else thresholds.anomaly.mad_multiplier * 1e9,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )
