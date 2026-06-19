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
from algotrading.infra.surfaces import (
    PROVENANCE_EXTRAPOLATED,
    CalendarViolation,
    SliceFit,
    classify_tenor_provenance,
    is_benign_a_floor,
    iv_space_fit_error,
    tenor_years,
)
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


def _scope_critical(
    *, status: str, severity: str, is_index: bool
) -> tuple[str, str]:
    """Scope a would-be CRITICAL verdict to the underlying's role (ADR 0060).

    The strict CRITICAL gates were calibrated for the one tradeable index (SX5E). When the same
    gate runs on an illiquid single-name constituent surface, a genuine-on-the-index defect is
    expected noise on the constituent, so it must NOTICE, never PAGE, and never block the date from
    banking. We downgrade a constituent's CRITICAL fail to a WARNING in BOTH dimensions:

    - ``severity`` CRITICAL -> WARNING so ``escalation_level`` yields NOTICE, not PAGE.
    - ``qc_status`` FAIL -> WARN so the report's worst-of ``overall_status`` is at worst WARN, not
      FAIL, on a constituent-only failure (banking keys off the paging escalation, not the raw
      status, but keeping the two aligned keeps the report honest).

    ``is_index=True`` (the default for every pre-existing caller) is a no-op: the index stays
    strictly CRITICAL/FAIL. Only the severity-CRITICAL verdict is touched; a WARNING or a PASS the
    check already produced is returned unchanged regardless of role.
    """
    if is_index or severity != SEVERITY_CRITICAL or status != STATUS_FAIL:
        return status, severity
    return STATUS_WARN, SEVERITY_WARNING


def check_collector_continuity(
    summary: CollectorContinuityInput,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
    is_index: bool = True,
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
    status, severity = _scope_critical(
        status=status, severity=SEVERITY_CRITICAL, is_index=is_index
    )
    return build_result(
        check_name=CHECK_COLLECTOR_CONTINUITY,
        target_key=summary.session_id,
        status=status,
        severity=severity,
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
    is_index: bool = True,
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
    status, severity = _scope_critical(
        status=status, severity=SEVERITY_CRITICAL, is_index=is_index
    )
    return build_result(
        check_name=CHECK_UNDERLYING_QUOTE_HEALTH,
        target_key=target,
        status=status,
        severity=severity,
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


_RHO_RAIL_BOUND_HITS = frozenset({"rho_lower", "rho_upper"})


def _is_rho_rail(bound_hit: str) -> bool:
    """A skew parameter pinned to its bound (steep smile), not a structural defect.

    SVI ρ is the skew; a genuinely steep skew (a long-maturity or steep-skew slice) drives ρ to its
    bound, but the fit is otherwise sound. We demote a ρ-rail-ONLY hit to a non-blocking note when
    the slice is arb-free, converged, and tracks the market IV cloud (mirroring how the benign
    ``a_lower`` parametrization sink is already treated). A real defect (arb violation,
    non-convergence, a different railed param, or a high IV-space error) still fails.
    """
    return bound_hit in _RHO_RAIL_BOUND_HITS


def check_surface_fit_error(
    fit: SliceFit,
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    tol = thresholds.fit_tolerance
    rmse_ok = fit.rmse <= tol.max_surface_rmse
    minimum_total_variance = (
        fit.svi.minimum_total_variance() if fit.svi is not None else None
    )

    # IV-SPACE limbs (vol points, T-invariant) — what a PM reads, and the teeth the total-variance
    # limb is blind to at short maturities. `iv_rmse` is the aggregate error; `iv_outlier_fraction`
    # catches a clean aggregate fit contaminated by a few stale quotes scattered off the curve.
    iv_error = iv_space_fit_error(fit)
    iv_rmse = iv_error.iv_rmse
    iv_outlier_fraction = iv_error.outlier_fraction
    iv_rmse_low = iv_rmse is not None and iv_rmse <= tol.warn_iv_rmse
    iv_rmse_warn = iv_rmse is not None and tol.warn_iv_rmse < iv_rmse <= tol.max_iv_rmse
    iv_rmse_fail = iv_rmse is not None and iv_rmse > tol.max_iv_rmse
    iv_outlier_fail = (
        iv_outlier_fraction is not None
        and iv_outlier_fraction > tol.max_iv_outlier_fraction
    )

    benign_bound_hits = [
        name
        for name in fit.bound_hits
        if is_benign_a_floor(name, minimum_total_variance=minimum_total_variance)
    ]
    remaining_hits = [name for name in fit.bound_hits if name not in benign_bound_hits]

    # A ρ-rail (steep-skew) hit is demoted to a NON-BLOCKING note when the slice is otherwise clean:
    # arb-free, converged, and a low IV-space RMSE. A steep skew is not a defect (mirrors a_lower).
    other_arb_free = fit.arb_free
    other_converged = fit.converged is not False
    rho_demotable = other_arb_free and other_converged and iv_rmse_low
    rho_rail_hits = [name for name in remaining_hits if _is_rho_rail(name)]
    genuine_bound_hits = [
        name
        for name in remaining_hits
        if not (_is_rho_rail(name) and rho_demotable)
    ]
    demoted_rho_hits = [name for name in rho_rail_hits if rho_demotable]

    fail_reasons: list[str] = []
    if not fit.arb_free:
        fail_reasons.append("arb_violation")
    if genuine_bound_hits:
        fail_reasons.append(f"bound_hit:{','.join(genuine_bound_hits)}")
    if fit.converged is False:
        fail_reasons.append("not_converged")
    if iv_rmse_fail:
        fail_reasons.append("iv_rmse_high")
    if iv_outlier_fail:
        fail_reasons.append("iv_outlier_scatter")
    if not rmse_ok:
        fail_reasons.append("total_variance_rmse_high")

    notes: list[str] = []
    if demoted_rho_hits:
        notes.append(f"rho_rail:{','.join(demoted_rho_hits)}")
    if iv_rmse_warn:
        notes.append("iv_rmse_elevated")

    if fail_reasons:
        status = STATUS_FAIL
    elif notes:
        status = STATUS_WARN
    else:
        status = STATUS_PASS

    context = {
        "underlying": fit.underlying,
        "failing_maturity": fit.maturity_years,
        "rmse": fit.rmse,
        "rmse_ok": rmse_ok,
        "iv_rmse": iv_rmse,
        "iv_outlier_fraction": iv_outlier_fraction,
        "iv_point_count": iv_error.point_count,
        "warn_iv_rmse": tol.warn_iv_rmse,
        "max_iv_rmse": tol.max_iv_rmse,
        "max_iv_outlier_fraction": tol.max_iv_outlier_fraction,
        "method": fit.method,
        "n_points": fit.n_points,
        "max_surface_rmse": tol.max_surface_rmse,
        "arb_free": fit.arb_free,
        "bound_hits": list(fit.bound_hits),
        "benign_bound_hits": benign_bound_hits,
        "demoted_rho_rail_hits": demoted_rho_hits,
        "minimum_total_variance": minimum_total_variance,
        "converged": fit.converged,
        "degeneracy_reasons": fail_reasons,
        "notes": notes,
    }
    # Headline measured_value is the IV-space RMSE (vol points) — the PM-legible error — when it
    # exists, else the total-variance RMSE (a sparse/reconstructed slice with no comparable points).
    measured = iv_rmse if iv_rmse is not None else fit.rmse
    return build_result(
        check_name=CHECK_SURFACE_FIT_ERROR,
        target_key=f"{fit.underlying}@{fit.maturity_years:g}",
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=measured,
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
    is_index: bool = True,
) -> QcResult:
    abs_tol = thresholds.grid.calendar_abs_variance_tol
    rel_tol = thresholds.grid.calendar_rel_variance_tol
    ultra_short = thresholds.grid.ultra_short_maturity_years

    count = len(violations)
    material: list[CalendarViolation] = []
    noise: list[CalendarViolation] = []
    worst = None
    worst_gap = 0.0
    for violation in violations:
        gap = violation.w_short - violation.w_long
        if worst is None or gap > worst_gap:
            worst = violation
            worst_gap = gap
        if _is_material_calendar_violation(
            violation, abs_tol=abs_tol, rel_tol=rel_tol, ultra_short=ultra_short
        ):
            material.append(violation)
        else:
            noise.append(violation)

    # Page CRITICAL only on a MATERIAL/GROSS inversion (blueprint 02-math-framework); a
    # sub-threshold or ultra-short-maturity wiggle is at most a WARNING (ADR 0052).
    if material:
        status = STATUS_FAIL
        severity = SEVERITY_CRITICAL
    elif noise:
        status = STATUS_WARN
        severity = SEVERITY_WARNING
    else:
        status = STATUS_PASS
        severity = SEVERITY_CRITICAL

    context: dict[str, object] = {
        "underlying": underlying,
        "violation_count": count,
        "material_count": len(material),
        "noise_count": len(noise),
        "calendar_abs_variance_tol": abs_tol,
        "calendar_rel_variance_tol": rel_tol,
        "ultra_short_maturity_years": ultra_short,
    }
    if worst is not None:
        context.update(
            {
                "failing_maturity_short": worst.maturity_short,
                "failing_maturity_long": worst.maturity_long,
                "failing_k": worst.k,
                "w_short": worst.w_short,
                "w_long": worst.w_long,
                "worst_variance_gap": worst_gap,
            }
        )
    measured = float(len(material)) if material else float(count)
    status, severity = _scope_critical(status=status, severity=severity, is_index=is_index)
    return build_result(
        check_name=CHECK_CALENDAR_SANITY,
        target_key=underlying,
        status=status,
        severity=severity,
        measured_value=measured,
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def _is_material_calendar_violation(
    violation: CalendarViolation,
    *,
    abs_tol: float,
    rel_tol: float,
    ultra_short: float,
) -> bool:
    """A calendar inversion is material only when it is GROSS (blueprint Eq. 21 diagnostic).

    Gross means the short-minus-long total-variance gap clears BOTH an absolute tolerance and a
    fraction of the long-leg variance, AND the short leg is not an ultra-short (numerically
    noisy) maturity. Anything below is sub-threshold noise — a WARNING, never a page.
    """
    gap = violation.w_short - violation.w_long
    if gap <= 0.0:
        return False
    if violation.maturity_short < ultra_short:
        return False
    if gap <= abs_tol:
        return False
    reference = abs(violation.w_long)
    return not (reference > 0.0 and gap <= rel_tol * reference)


def check_greek_sanity(
    line: PositionRisk,
    *,
    broker: BrokerGreeks | None = None,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
    is_index: bool = True,
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
    status, severity = _scope_critical(
        status=status, severity=SEVERITY_CRITICAL, is_index=is_index
    )
    return build_result(
        check_name=CHECK_GREEK_SANITY,
        target_key=contract_key,
        status=status,
        severity=severity,
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
    is_index: bool = True,
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
    status, severity = _scope_critical(
        status=status, severity=SEVERITY_CRITICAL, is_index=is_index
    )
    return build_result(
        check_name=CHECK_SCENARIO_COMPLETENESS,
        target_key=portfolio_id,
        status=status,
        severity=severity,
        measured_value=float(len(missing)),
        threshold_version=thresholds.version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def _liquid_span(
    tenor_grid: Sequence[str], floor_clearing: set[str],
) -> tuple[float, float] | None:
    """(min, max) maturity of the tenors that clear their within-liquid floor.

    This is the captured liquid maturity range — the "monitored maturities" of blueprint
    14-slos. Interior pinned tenors fall inside it (interpolatable, Eq. 22); edge pinned tenors
    fall outside it (extrapolation fallback, ADR 0052).
    """
    maturities = [tenor_years(t) for t in tenor_grid if t in floor_clearing]
    if not maturities:
        return None
    return min(maturities), max(maturities)


def check_tenor_coverage_floor(
    points: Sequence[GridPointInput],
    underlying: str,
    tenor_grid: Sequence[str],
    *,
    thresholds: QcThresholdConfig,
    run_id: str,
    run_ts: datetime,
    is_index: bool = True,
) -> QcResult:
    counts: dict[str, int] = {tenor: 0 for tenor in tenor_grid}
    for point in points:
        if point.tenor_label in counts:
            counts[point.tenor_label] += 1
    floors = {tenor: thresholds.grid.floor_for(tenor) for tenor in tenor_grid}
    floor_clearing = {tenor for tenor in tenor_grid if counts[tenor] >= floors[tenor]}
    span = _liquid_span(tenor_grid, floor_clearing)
    direct = [tenor_years(t) for t in floor_clearing]

    interior_tenors: list[str] = []
    interior_covered: list[str] = []
    critical_breaches: list[dict[str, object]] = []  # liquid-core collapse -> CRITICAL
    edge_warnings: list[dict[str, object]] = []  # extrapolated edge fallback -> WARNING
    worst_margin: float | None = None

    for tenor in tenor_grid:
        floor = floors[tenor]
        count = counts[tenor]
        margin = float(count - floor)
        if worst_margin is None or margin < worst_margin:
            worst_margin = margin
        provenance = classify_tenor_provenance(
            tenor_years(tenor), liquid_span=span, direct_maturities=direct
        )
        if count >= floor:
            # A liquid, monitored maturity that clears its own floor.
            interior_tenors.append(tenor)
            interior_covered.append(tenor)
            continue
        if provenance == PROVENANCE_EXTRAPOLATED:
            # Edge tenor beyond the liquid range — blueprint fallback, never a hard CRITICAL.
            edge_warnings.append(
                {"tenor": tenor, "measured": count, "floor": floor, "provenance": provenance}
            )
            continue
        # Interior pin below its floor.
        interior_tenors.append(tenor)
        if count == 0:
            # No direct capture, but bracketed by liquid neighbours → filled by Eq.-22
            # total-variance interpolation (covered, not a breach).
            interior_covered.append(tenor)
        else:
            # Partial capture inside the liquid range that fell short of the within-liquid
            # floor — a real liquid-core coverage collapse, the CRITICAL tooth.
            critical_breaches.append(
                {"tenor": tenor, "measured": count, "floor": floor, "provenance": provenance}
            )

    # The ≥95% monitored-coverage ratio is the second CRITICAL tooth (blueprint 14-slos): if the
    # liquid core collapses across many maturities, the ratio falls below the floor and pages.
    monitored = len(interior_tenors)
    covered = len(interior_covered)
    coverage_ratio = covered / monitored if monitored > 0 else 0.0
    min_ratio = thresholds.grid.monitored_coverage_ratio
    # A grid with no liquid maturity at all is a genuine collapse, not an edge fallback.
    ratio_breach = (span is None) or (coverage_ratio < min_ratio)

    if critical_breaches or ratio_breach:
        status = STATUS_FAIL
        severity = SEVERITY_CRITICAL
    elif edge_warnings:
        status = STATUS_WARN
        severity = SEVERITY_WARNING
    else:
        status = STATUS_PASS
        severity = SEVERITY_CRITICAL

    measured = worst_margin if worst_margin is not None else 0.0
    context = {
        "underlying": underlying,
        "pinned_tenor_count": len(tenor_grid),
        "monitored_tenor_count": monitored,
        "monitored_covered_count": covered,
        "coverage_ratio": coverage_ratio,
        "min_coverage_ratio": min_ratio,
        "breach_count": len(critical_breaches),
        "breaching_tenors": critical_breaches,
        "edge_warning_count": len(edge_warnings),
        "edge_tenors": edge_warnings,
    }
    if status == STATUS_FAIL:
        _log.warning(
            "qc tenor_coverage_floor fail: underlying=%s critical=%d ratio=%.3f",
            underlying,
            len(critical_breaches),
            coverage_ratio,
            extra={
                "underlying": underlying,
                "breaching_tenors": critical_breaches,
                "coverage_ratio": coverage_ratio,
            },
        )
    status, severity = _scope_critical(status=status, severity=severity, is_index=is_index)
    return build_result(
        check_name=CHECK_TENOR_COVERAGE_FLOOR,
        target_key=underlying,
        status=status,
        severity=severity,
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
    is_index: bool = True,
) -> QcResult:
    by_tenor: dict[str, list[float]] = {tenor: [] for tenor in tenor_grid}
    for point in points:
        if point.tenor_label in by_tenor:
            by_tenor[point.tenor_label].append(point.target_delta)
    band_low = thresholds.grid.band_low_delta
    band_high = thresholds.grid.band_high_delta
    max_step = thresholds.grid.max_delta_step
    edge_tol = 1e-9

    # The liquid range for the band check is the maturities whose ±band is complete (no gaps).
    # Inside it, an incomplete band is a real liquid-core defect → CRITICAL; outside it (the
    # extrapolated edges, e.g. 2y/3y or sub-front 10d) a partial band is a WARNING (ADR 0052).
    band_reasons: dict[str, list[dict[str, object]]] = {}
    complete_tenors: set[str] = set()
    for tenor in tenor_grid:
        deltas = sorted(by_tenor[tenor])
        reasons = _band_gap_reasons(
            deltas, band_low=band_low, band_high=band_high, max_step=max_step, edge_tol=edge_tol,
        )
        band_reasons[tenor] = reasons
        if not reasons:
            complete_tenors.add(tenor)
    span = _liquid_span(tenor_grid, complete_tenors)
    direct = [tenor_years(t) for t in complete_tenors]

    critical_gaps: list[dict[str, object]] = []
    edge_gaps: list[dict[str, object]] = []
    for tenor in tenor_grid:
        reasons = band_reasons[tenor]
        if not reasons:
            continue
        provenance = classify_tenor_provenance(
            tenor_years(tenor), liquid_span=span, direct_maturities=direct
        )
        entry = {
            "tenor": tenor, "point_count": len(by_tenor[tenor]),
            "missing": reasons, "provenance": provenance,
        }
        if provenance == PROVENANCE_EXTRAPOLATED:
            edge_gaps.append(entry)
        else:
            critical_gaps.append(entry)

    if critical_gaps:
        status = STATUS_FAIL
        severity = SEVERITY_CRITICAL
    elif edge_gaps:
        status = STATUS_WARN
        severity = SEVERITY_WARNING
    else:
        status = STATUS_PASS
        severity = SEVERITY_CRITICAL

    all_gaps = critical_gaps + edge_gaps
    context = {
        "underlying": underlying,
        "band_low_delta": band_low,
        "band_high_delta": band_high,
        "max_delta_step": max_step,
        "pinned_tenor_count": len(tenor_grid),
        "gap_count": len(critical_gaps),
        "band_gaps": critical_gaps,
        "edge_gap_count": len(edge_gaps),
        "edge_band_gaps": edge_gaps,
    }
    if status == STATUS_FAIL:
        _log.warning(
            "qc delta_band_completeness fail: underlying=%s gaps=%d",
            underlying,
            len(critical_gaps),
            extra={"underlying": underlying, "band_gaps": critical_gaps},
        )
    status, severity = _scope_critical(status=status, severity=severity, is_index=is_index)
    return build_result(
        check_name=CHECK_DELTA_BAND_COMPLETENESS,
        target_key=underlying,
        status=status,
        severity=severity,
        measured_value=float(len(all_gaps)),
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
    is_index: bool = True,
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
    status, severity = _scope_critical(
        status=status, severity=SEVERITY_CRITICAL, is_index=is_index
    )
    return build_result(
        check_name=CHECK_PUT_CALL_IV_SPREAD,
        target_key=underlying,
        status=status,
        severity=severity,
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
