"""The QC / validation plane — named checks that prove the system's invariants hold.

This is the static, per-object validation plane for the platform: a library of pure,
named checks that each take a producing workstream's output and return a stamped
:class:`~algotrading.infra.contracts.QcResult`. The design rule is specificity. A QC
failure must name the exact maturity, quote, underlying, or solver that broke — a
generic red banner is the precise failure mode these checks exist to prevent — so
every failing result carries the offending object's name in its context payload.

Its sibling, the rolling-baseline anomaly plane, lives in
:mod:`algotrading.infra.validation`; both planes collapse into the one persisted
``triage_records`` shape (``contracts.TriageRecord``).

The fastest path to a daily verdict:

    from algotrading.core.config import PlatformConfig            # load it
    from algotrading.infra.qc import thresholds_from_config, build_report
    from algotrading.infra.qc import check_collector_continuity   # + the checks you have inputs for

    thresholds = thresholds_from_config(config.qc_threshold)
    results = [check_collector_continuity(summary, thresholds=thresholds,
                                          run_id=run_id, run_ts=run_ts), ...]
    report = build_report(results, run_id=run_id, run_ts=run_ts)
    level = escalation_level(report)        # none / notice / page

Every check takes its ``run_id`` and ``run_ts`` injected — never read from a clock —
so a check is a pure function of its inputs and reproduces byte-for-byte in replay.
"""

from __future__ import annotations

from .checks import (
    CHECK_ANOMALY,
    CHECK_CALENDAR_SANITY,
    CHECK_COLLECTOR_CONTINUITY,
    CHECK_FORWARD_STABILITY,
    CHECK_GREEK_SANITY,
    CHECK_IV_SOLVER_CONVERGENCE,
    CHECK_NAMES,
    CHECK_OPTION_CHAIN_COVERAGE,
    CHECK_PARITY_RESIDUAL,
    CHECK_SCENARIO_COMPLETENESS,
    CHECK_SURFACE_FIT_ERROR,
    CHECK_UNDERLYING_QUOTE_HEALTH,
    check_calendar_sanity,
    check_collector_continuity,
    check_forward_stability,
    check_greek_sanity,
    check_iv_solver_convergence,
    check_option_chain_coverage,
    check_parity_residual,
    check_scenario_completeness,
    check_surface_fit_error,
    check_underlying_quote_health,
    detect_anomaly,
    robust_z_score,
)
from .errors import (
    ContractKeyMismatchError,
    EmptyBaselineError,
    QcError,
)
from .inputs import CollectorContinuityInput
from .report import (
    ESCALATION_LEVELS,
    ESCALATION_NONE,
    ESCALATION_NOTICE,
    ESCALATION_PAGE,
    QcReport,
    build_report,
    escalation_level,
    named_offender,
    result_headline,
)
from .result import (
    QC_SEVERITIES,
    QC_STATUSES,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    build_result,
    deserialize_context,
    serialize_context,
)
from .thresholds import QcThresholds, thresholds_from_config

__all__ = [
    "CHECK_ANOMALY",
    "CHECK_CALENDAR_SANITY",
    "CHECK_COLLECTOR_CONTINUITY",
    "CHECK_FORWARD_STABILITY",
    "CHECK_GREEK_SANITY",
    "CHECK_IV_SOLVER_CONVERGENCE",
    "CHECK_NAMES",
    "CHECK_OPTION_CHAIN_COVERAGE",
    "CHECK_PARITY_RESIDUAL",
    "CHECK_SCENARIO_COMPLETENESS",
    "CHECK_SURFACE_FIT_ERROR",
    "CHECK_UNDERLYING_QUOTE_HEALTH",
    "ESCALATION_LEVELS",
    "ESCALATION_NONE",
    "ESCALATION_NOTICE",
    "ESCALATION_PAGE",
    "QC_SEVERITIES",
    "QC_STATUSES",
    "SEVERITY_CRITICAL",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_WARN",
    "CollectorContinuityInput",
    "ContractKeyMismatchError",
    "EmptyBaselineError",
    "QcError",
    "QcReport",
    "QcThresholds",
    "build_report",
    "build_result",
    "check_calendar_sanity",
    "check_collector_continuity",
    "check_forward_stability",
    "check_greek_sanity",
    "check_iv_solver_convergence",
    "check_option_chain_coverage",
    "check_parity_residual",
    "check_scenario_completeness",
    "check_surface_fit_error",
    "check_underlying_quote_health",
    "deserialize_context",
    "detect_anomaly",
    "escalation_level",
    "named_offender",
    "result_headline",
    "robust_z_score",
    "serialize_context",
    "thresholds_from_config",
]
