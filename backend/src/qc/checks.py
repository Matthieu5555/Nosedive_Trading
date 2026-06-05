"""The ten named QC checks plus rolling-baseline anomaly detection.

Each check is a pure function: it takes the object a producing workstream emitted,
the :class:`~qc.thresholds.QcThresholds` bundle, and an injected ``run_id`` /
``run_ts`` (never a clock), and returns one :class:`contracts.QcResult`. A check
never raises on a *failing* target — a fail is a normal verdict carried in the
result. It raises only when its inputs are self-contradictory (a mis-wired join),
via the QC-owned exceptions, because that is a wiring bug, not a data quality fail.

The non-negotiable property of every failing result is specificity: the context
payload names the exact maturity, quote, underlying, or solver that failed, under
an explicit key. A generic "QC failed" banner is the precise failure mode these
checks exist to prevent, so the name is in the data, not just in a log line.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

import structlog

from collectors import CollectorSummary
from contracts import QcResult
from forwards import ForwardEstimate, ParityLine
from iv import STATUS_CONVERGED, IvResult
from risk import BrokerGreeks, GreekDiscrepancy, PositionRisk, reconcile
from snapshots import SnapshotBatch
from surfaces import CalendarViolation, SliceFit

from .errors import ContractKeyMismatchError, EmptyBaselineError
from .result import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    build_result,
)
from .thresholds import QcThresholds

_log = structlog.get_logger(__name__)

# The check-name constants double as the ``check_name`` stamped on each QcResult and
# the keys the report/triage layer groups by, so the name lives in exactly one place.
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
CHECK_ANOMALY = "anomaly_detection"

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
)

# The quote-QC status (snapshots.QUOTE_STATUSES) that means a quote passed and may
# feed analytics. Only usable quotes are judged by the quote-health/coverage checks.
_USABLE_QUOTE_STATUS = "usable"


def check_collector_continuity(
    summary: CollectorSummary,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Did the collector run cleanly: few gaps, enough of the universe covered.

    Measured value is the session's gap count. It fails when gaps exceed
    ``max_gap_count`` or coverage falls below ``min_coverage_ratio``, warns on a
    smaller gap count, and names the offending ``session_id`` (and the coverage
    fraction) in the context so an operator sees which session to investigate.
    """
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
    if gap_count > thresholds.max_gap_count or coverage_ratio < thresholds.min_coverage_ratio:
        status = STATUS_FAIL
    elif gap_count > thresholds.warn_gap_count:
        status = STATUS_WARN
    else:
        status = STATUS_PASS
    if status == STATUS_FAIL:
        _log.warning(
            "qc.collector_continuity.fail",
            session_id=summary.session_id,
            gap_count=gap_count,
            coverage_ratio=coverage_ratio,
        )
    return build_result(
        check_name=CHECK_COLLECTOR_CONTINUITY,
        target_key=summary.session_id,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=float(gap_count),
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_underlying_quote_health(
    batch: SnapshotBatch,
    underlying_instrument_keys: Sequence[str],
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Is each underlying's own quote tight and fresh enough to anchor analytics.

    ``underlying_instrument_keys`` are the snapshot keys of the bare underlyings (a
    STK key, distinct from the option legs that hang off it), so the check looks only
    at the anchor quotes. It scans the usable underlying snapshots and reports the
    widest spread seen. It fails when any usable underlying quote's ``spread_pct``
    exceeds ``max_spread_pct``, naming the exact ``instrument_key`` of the worst quote
    so the operator sees which quote, not just that "a quote" was bad.
    """
    anchors = set(underlying_instrument_keys)
    worst_key = ""
    worst_spread = 0.0
    seen = 0
    for assessed in batch.assessed:
        snap = assessed.snapshot
        if snap.instrument_key not in anchors:
            continue
        if assessed.assessment.status != _USABLE_QUOTE_STATUS:
            continue
        seen += 1
        if snap.spread_pct > worst_spread:
            worst_spread = snap.spread_pct
            worst_key = snap.instrument_key
    status = STATUS_PASS if worst_spread <= thresholds.max_spread_pct else STATUS_FAIL
    target = worst_key if worst_key else (sorted(anchors)[0] if anchors else "")
    context = {
        "failing_quote": worst_key,
        "worst_spread_pct": worst_spread,
        "max_spread_pct": thresholds.max_spread_pct,
        "usable_quote_count": seen,
    }
    return build_result(
        check_name=CHECK_UNDERLYING_QUOTE_HEALTH,
        target_key=target,
        status=status,
        severity=SEVERITY_CRITICAL,
        measured_value=worst_spread,
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_option_chain_coverage(
    batch: SnapshotBatch,
    underlying: str,
    expected_contract_keys: Sequence[str],
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Did enough of the expected option chain arrive with a usable quote.

    Compares the usable option snapshots for the underlying against the expected
    chain membership (from the instrument master). Measured value is the usable
    count. It fails when the usable count is below ``min_chain_count`` and names the
    underlying plus the *specific missing contract keys*, so an operator sees which
    strikes are absent, not merely that the chain is "incomplete".
    """
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
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_forward_stability(
    estimate: ForwardEstimate,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Is the recovered forward stable: tight parity residuals and enough confidence.

    Measured value is the parity-line residual MAD. It fails when the MAD exceeds
    ``max_residual_mad`` or the estimate confidence is below
    ``min_forward_confidence``, and names the exact ``underlying`` and
    ``maturity_years`` of the unstable forward, plus the quality label and reason
    code C already attached.
    """
    target_key = f"{estimate.underlying}@{estimate.maturity_years:g}"
    unstable = (
        estimate.residual_mad > thresholds.max_residual_mad
        or estimate.confidence < thresholds.min_forward_confidence
    )
    status = STATUS_FAIL if unstable else STATUS_PASS
    context = {
        "underlying": estimate.underlying,
        "failing_maturity": estimate.maturity_years,
        "residual_mad": estimate.residual_mad,
        "confidence": estimate.confidence,
        "quality_label": estimate.quality_label,
        "reason_code": estimate.reason_code,
        "max_residual_mad": thresholds.max_residual_mad,
        "min_confidence": thresholds.min_forward_confidence,
    }
    return build_result(
        check_name=CHECK_FORWARD_STABILITY,
        target_key=target_key,
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=estimate.residual_mad,
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_parity_residual(
    line: ParityLine,
    underlying: str,
    maturity_years: float,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Are the per-strike put-call-parity residuals within tolerance.

    Measured value is the largest absolute parity residual on the line. It fails
    when that exceeds ``max_parity_residual``, naming the underlying and maturity of
    the offending fit plus the index of the worst residual, so the operator sees the
    specific strike-pair that broke parity rather than a blanket "parity off".
    """
    residuals = line.residuals
    worst_index = -1
    worst_abs = 0.0
    for index, residual in enumerate(residuals):
        magnitude = abs(residual)
        if magnitude > worst_abs:
            worst_abs = magnitude
            worst_index = index
    status = STATUS_PASS if worst_abs <= thresholds.max_parity_residual else STATUS_FAIL
    context = {
        "underlying": underlying,
        "failing_maturity": maturity_years,
        "worst_residual": worst_abs,
        "worst_residual_index": worst_index,
        "residual_count": len(residuals),
        "max_parity_residual": thresholds.max_parity_residual,
    }
    return build_result(
        check_name=CHECK_PARITY_RESIDUAL,
        target_key=f"{underlying}@{maturity_years:g}",
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=worst_abs,
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_iv_solver_convergence(
    results: Sequence[IvResult],
    target_key: str,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """What fraction of the IV inversions failed to converge.

    Measured value is the non-convergence ratio across the supplied solver results.
    It fails when that exceeds ``max_non_convergence_ratio`` and names the specific
    failing solver contract keys (the first few) plus their statuses, so an operator
    sees which contracts the solver could not invert, not merely a failure rate.
    """
    total = len(results)
    failed = [r for r in results if r.status != STATUS_CONVERGED]
    ratio = len(failed) / total if total > 0 else 0.0
    status = STATUS_PASS if ratio <= thresholds.max_non_convergence_ratio else STATUS_FAIL
    failing_solvers = [
        {"contract_key": r.contract_key, "status": r.status} for r in failed
    ]
    context = {
        "target": target_key,
        "non_convergence_ratio": ratio,
        "failed_count": len(failed),
        "total_count": total,
        "max_non_convergence_ratio": thresholds.max_non_convergence_ratio,
        "failing_solvers": failing_solvers,
    }
    return build_result(
        check_name=CHECK_IV_SOLVER_CONVERGENCE,
        target_key=target_key,
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=ratio,
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_surface_fit_error(
    fit: SliceFit,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Is the slice fit tight: per-maturity RMSE within tolerance.

    Measured value is the slice RMSE (total-variance units). It fails when that
    exceeds ``max_surface_rmse``, naming the underlying and the exact maturity of the
    badly-fit slice plus the fit method, so the operator knows which expiry's smile
    to investigate.
    """
    status = STATUS_PASS if fit.rmse <= thresholds.max_surface_rmse else STATUS_FAIL
    context = {
        "underlying": fit.underlying,
        "failing_maturity": fit.maturity_years,
        "rmse": fit.rmse,
        "method": fit.method,
        "n_points": fit.n_points,
        "max_surface_rmse": thresholds.max_surface_rmse,
    }
    return build_result(
        check_name=CHECK_SURFACE_FIT_ERROR,
        target_key=f"{fit.underlying}@{fit.maturity_years:g}",
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=fit.rmse,
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_calendar_sanity(
    violations: Sequence[CalendarViolation],
    underlying: str,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Is the surface calendar-arbitrage free across maturities.

    Measured value is the count of calendar no-arb violations. Any violation fails
    the check (total variance must be non-decreasing in maturity, ADR/Eq 21). The
    context names the worst-crossing maturity pair and log-moneyness, so the operator
    sees exactly which short/long maturities cross rather than a generic "arb" flag.
    """
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
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_greek_sanity(
    line: PositionRisk,
    *,
    broker: BrokerGreeks | None = None,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Are the line's Greeks well-formed, and do they reconcile to the broker.

    Two layers. First, sign/finiteness sanity that C guarantees and D inherits:
    ``gamma >= 0``, ``vega >= 0``, a call ``delta in [0, 1]`` / put ``delta in
    [-1, 0]``, and all Greeks finite. Second, if a ``broker`` row is supplied, the
    computed Greeks must reconcile within tolerance.

    Folds in ADR 0006's deferred precondition: ``risk.reconcile`` never asserts the
    broker row is for *this* contract, so a mis-wired join would compare the wrong
    Greek silently. This check makes ``broker.contract_key == line.contract_key`` a
    hard precondition and raises :class:`ContractKeyMismatchError` (naming both keys)
    rather than producing a meaningless discrepancy.

    Measured value is the number of breaches. On failure the context names the exact
    ``contract_key`` and the offending Greeks.
    """
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
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def check_scenario_completeness(
    produced_cells: Sequence[tuple[str, str]],
    expected_cells: Sequence[tuple[str, str]],
    portfolio_id: str,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Did every expected (scenario, contract) stress cell get produced.

    Each cell is a ``(scenario_id, contract_key)`` pair. Measured value is the count
    of expected cells that were not produced. Any missing cell fails the check, and
    the context names the specific missing ``scenario_id`` / ``contract_key`` pairs,
    so an operator sees exactly which stress did not run rather than a count alone.
    """
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
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )


def _median(values: Sequence[float]) -> float:
    """The median of a non-empty sequence (no external dependency)."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def robust_z_score(observed: float, baseline: Sequence[float]) -> float:
    """A median/MAD robust z-score of ``observed`` against a rolling ``baseline``.

    Uses the median absolute deviation rather than the standard deviation so a single
    earlier spike in the baseline does not inflate the scale and mask a new one. When
    the baseline has no spread (all equal), a value equal to the median scores 0 and
    any departure scores infinity — the only honest answers for a degenerate scale.
    Raises :class:`EmptyBaselineError` for an empty baseline, since "is this a spike"
    has no answer without a reference.
    """
    if not baseline:
        raise EmptyBaselineError(observed)
    center = _median(baseline)
    deviations = [abs(value - center) for value in baseline]
    mad = _median(deviations)
    if mad == 0.0:
        return 0.0 if observed == center else math.inf
    # 1.4826 scales MAD to a standard-deviation-equivalent for normal data.
    return abs(observed - center) / (1.4826 * mad)


def detect_anomaly(
    observed: float,
    baseline: Sequence[float],
    metric_name: str,
    target_key: str,
    *,
    thresholds: QcThresholds,
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Flag ``observed`` as an anomaly when it sits too far from its rolling baseline.

    Measured value is the robust z-score. It fails when the score exceeds
    ``anomaly_mad_multiplier`` MADs from the baseline median, naming the metric and
    target so the operator sees *which* metric spiked. A value within the band
    passes. The baseline is the recent history of the same metric; an empty baseline
    raises :class:`EmptyBaselineError`.
    """
    score = robust_z_score(observed, baseline)
    status = STATUS_FAIL if score > thresholds.anomaly_mad_multiplier else STATUS_PASS
    context = {
        "metric": metric_name,
        "target": target_key,
        "observed": observed,
        "baseline_median": _median(baseline),
        "robust_z_score": score if math.isfinite(score) else "inf",
        "baseline_size": len(baseline),
        "mad_multiplier": thresholds.anomaly_mad_multiplier,
    }
    return build_result(
        check_name=CHECK_ANOMALY,
        target_key=target_key,
        status=status,
        severity=SEVERITY_WARNING,
        measured_value=score if math.isfinite(score) else thresholds.anomaly_mad_multiplier * 1e9,
        threshold_version=thresholds.threshold_version,
        context=context,
        run_id=run_id,
        run_ts=run_ts,
    )
