"""The ten named QC checks plus rolling-baseline anomaly detection.

Each check is a pure function: it takes the object a producing workstream emitted,
the typed, hashed :class:`~algotrading.core.config.QcThresholdConfig` (every economic
cut-off lives there, ADR 0028 — the nested blocks name which config block owns each
cut-off), and an injected ``run_id`` / ``run_ts`` (never a clock), and returns one
:class:`~algotrading.infra.contracts.QcResult`. A check never raises on a *failing*
target — a fail is a normal verdict carried in the result. It raises only when its
inputs are self-contradictory (a mis-wired join), via the QC-owned exceptions, because
that is a wiring bug, not a data quality fail.

The non-negotiable property of every failing result is specificity: the context
payload names the exact maturity, quote, underlying, or solver that failed, under
an explicit key. A generic "QC failed" banner is the precise failure mode these
checks exist to prevent, so the name is in the data, not just in a log line.
"""

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
from .inputs import CollectorContinuityInput, GridPointInput
from .result import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    build_result,
)

_log = get_logger(__name__)

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
CHECK_TENOR_COVERAGE_FLOOR = "tenor_coverage_floor"
CHECK_DELTA_BAND_COMPLETENESS = "delta_band_completeness"
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
    CHECK_TENOR_COVERAGE_FLOOR,
    CHECK_DELTA_BAND_COMPLETENESS,
)

# The quote-QC status (snapshots.QUOTE_STATUSES) that means a quote passed and may
# feed analytics. Only usable quotes are judged by the quote-health/coverage checks.
_USABLE_QUOTE_STATUS = "usable"


def check_collector_continuity(
    summary: CollectorContinuityInput,
    *,
    thresholds: QcThresholdConfig,
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
    """Is the recovered forward stable: tight parity residuals and enough confidence.

    Measured value is the parity-line residual MAD **as a fraction of the forward**
    (``residual_mad / F``), not absolute price points — an absolute cut-off was an
    always-FAIL false positive on a 7400-pt index. It fails when that relative MAD
    exceeds ``max_rel_residual_mad`` or the estimate confidence is below
    ``min_forward_confidence``, and names the exact ``underlying`` and
    ``maturity_years`` of the unstable forward, plus the quality label and reason
    code already attached.
    """
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
    """Are the per-strike put-call-parity residuals within tolerance.

    Measured value is the largest parity residual on the line **as a fraction of the
    forward** (``|residual| / F``), not absolute price points — an absolute cut-off was
    an always-FAIL false positive on a 7400-pt index (worst residuals naturally O(1-100)
    pts there). It fails when that relative residual exceeds ``max_rel_parity_residual``,
    naming the underlying and maturity of the offending fit plus the index of the worst
    residual, so the operator sees the specific strike-pair that broke parity rather than
    a blanket "parity off".
    """
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
    """What fraction of the IV inversions failed to converge.

    Measured value is the non-convergence ratio across the supplied solver results.
    It fails when that exceeds ``max_non_convergence_ratio`` and names the specific
    failing solver contract keys (the first few) plus their statuses, so an operator
    sees which contracts the solver could not invert, not merely a failure rate.
    """
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
    """Is the slice fit tight AND usable: RMSE within tolerance and not degenerate/arb-violating.

    Measured value is the slice RMSE (total-variance units). A low RMSE alone is **not**
    sufficient: a railed (a parameter pinned to its bound, e.g. ``rho`` at ±0.999) or
    degenerate (``sigma → 0``) slice is *over-fit* — it scores a tiny RMSE precisely because it
    is pathological — so RMSE-only would reward the pathology (An-3 / QC-2, seed #3, confirmed on
    real SPX data: 3/4 slices ``arb_free=False`` yet all PASS on RMSE). The fit-lane already
    *renders* such a smile with a flag (flag-not-reject); this gate must not report it as a
    **clean** fit. So PASS requires RMSE ≤ ``max_surface_rmse`` AND the butterfly verdict
    ``arb_free`` AND no ``bound_hits`` AND the optimizer not having failed. ``converged is None``
    (the non-SVI fallback, which has no optimizer) is unknown, not a failure — never penalised.
    """
    rmse_ok = fit.rmse <= thresholds.fit_tolerance.max_surface_rmse
    # Each reason is one labelled cause of a non-clean fit, surfaced in context so the operator
    # sees *why* a tiny-RMSE slice is flagged.
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
    """Are the line's Greeks well-formed, and do they reconcile to the broker.

    Two layers. First, sign/finiteness sanity that the analytics core guarantees and
    risk inherits: ``gamma >= 0``, ``vega >= 0``, a call ``delta in [0, 1]`` / put
    ``delta in [-1, 0]``, and all Greeks finite. Second, if a ``broker`` row is
    supplied, the computed Greeks must reconcile within tolerance.

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
    """Does every pinned tenor clear its per-tenor coverage floor on the grid.

    ``points`` are one underlying's projected grid cells (WS 1F's
    ``ProjectedOptionAnalytics`` rows, which satisfy :class:`GridPointInput`).
    ``tenor_grid`` is the P0.1 pinned tenor set (read from config, never from the data).
    For each pinned tenor it counts the cells with that ``tenor_label`` and compares the
    count to the tenor's configured floor (``>=`` passes — boundary-exact passes, the
    thresholds convention). A pinned tenor *absent entirely* (zero cells) is a breach, not
    a skip — the count is simply zero. A pinned tenor with **no** configured floor is a
    config error: :meth:`GridQcConfig.floor_for` raises rather than defaulting to zero,
    so a mis-keyed grid fails loudly instead of passing a tenor for free.

    Measured value is the worst margin across tenors (lowest ``count - floor``): negative
    when any tenor is short, ``>= 0`` when all clear. The context names the *specific*
    breaching tenors with measured-vs-floor counts (mirroring
    :func:`check_option_chain_coverage`'s "name the missing contracts" style), so an
    operator sees *which* tenor is thin, not merely that coverage is low; the passing
    tenors are not named.
    """
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
    # An empty pinned grid is a wiring bug, not a data fail; guard the margin default.
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
    """Do each pinned tenor's selected strikes span the Δ-band with no interior hole.

    For each pinned tenor it takes the selected cells' **signed band-axis** deltas
    (``target_delta``) and asserts they span the configured band — from ``band_low_delta``
    (the 30Δ-put edge) through ATM to ``band_high_delta`` (the 30Δ-call edge) — with no
    interior gap wider than ``max_delta_step``. The band edges and max-step come from
    **config**, never from the points themselves, so a thin chain *fails* rather than
    silently defining its own (narrower) band — the look-ahead-style trap the spec calls out.

    It spans ``target_delta`` (the band coordinate), **not** the realized greek ``delta``:
    the two ATM pillars sit at ``0.0`` on the band axis (their realized deltas are ≈ ±0.5),
    so only the band coordinate fills the ATM centre — spanning the realized delta would open
    a ``2·band_step`` hole across ATM and could never enforce the configured step. This is
    what makes ``max_delta_step == band_step`` actually *force* the emission grid (a dropped
    point opens a ``2·band_step`` gap and fails).

    Three degenerate shapes are explicit breaches, labelled, never a silent pass or a
    crash: an **empty** tenor (no cells), a **single-strike** tenor (cannot span a band),
    and an **all-one-side** chain (only puts or only calls — does not reach both edges).
    The context names the offending tenor and the missing band region (which edge is
    unreached, or the ``[lo, hi]`` interior gap), so an operator sees *where* the hole is.

    Measured value is the count of tenors with a band gap (0 on a fully-complete grid).
    """
    by_tenor: dict[str, list[float]] = {tenor: [] for tenor in tenor_grid}
    for point in points:
        if point.tenor_label in by_tenor:
            by_tenor[point.tenor_label].append(point.target_delta)
    band_low = thresholds.grid.band_low_delta
    band_high = thresholds.grid.band_high_delta
    max_step = thresholds.grid.max_delta_step
    # A tiny tolerance so a delta landing exactly on the configured edge (the 30Δ point) is
    # treated as reaching it, not as falling just short of it.
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


def _band_gap_reasons(
    deltas: Sequence[float],
    *,
    band_low: float,
    band_high: float,
    max_step: float,
    edge_tol: float,
) -> list[dict[str, object]]:
    """Name every way ``deltas`` fail to cover ``[band_low, band_high]`` (empty if complete).

    The sorted ``deltas`` must reach the low edge (some delta ``<= band_low``), reach the
    high edge (some delta ``>= band_high``), and have no interior gap between consecutive
    deltas wider than ``max_step``. Each shortfall is one labelled reason. An empty or
    single-element input cannot span the band, so it surfaces the unreached edges (and is
    flagged as too few points) rather than crashing.
    """
    reasons: list[dict[str, object]] = []
    if len(deltas) < 2:
        # Empty or single-strike: cannot span a two-sided band. Name it as such and report
        # the edges it does not reach so the missing region is explicit, not just "thin".
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
    """A median/MAD robust z-score of ``observed`` against a rolling ``baseline``, unsigned.

    The QC-plane view over the one shared implementation
    (:func:`algotrading.infra.utils.robust_zscore_vs_baseline`, ADR 0021): the magnitude
    of the signed score — the QC anomaly check bands on distance, not direction. A
    degenerate baseline (no spread) scores 0 on the median and infinity off it, exactly
    as before (``abs`` of the signed ±inf/0). Raises :class:`EmptyBaselineError` for an
    empty baseline, since "is this a spike" has no answer without a reference.
    """
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
    """Flag ``observed`` as an anomaly when it sits too far from its rolling baseline.

    Measured value is the robust z-score. It fails when the score exceeds
    ``anomaly_mad_multiplier`` MADs from the baseline median, naming the metric and
    target so the operator sees *which* metric spiked. A value within the band
    passes. The baseline is the recent history of the same metric; an empty baseline
    raises :class:`EmptyBaselineError`.
    """
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
