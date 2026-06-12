"""The daily QC report and escalation model.

These pin the roll-up behavior: overall status is the worst single status, an empty
run is a clean pass (no invented failure), and escalation collapses a report to one
signal. Inputs are real ``QcResult`` rows produced by the actual checks, so the report
is tested against the data the framework emits, not hand-rolled stand-ins.

The worst-first *triage ordering* and offender-naming headline now live on the unified
``TriageRecord`` shape — see ``test_triage.py``. The legacy in-memory ``TriageRow`` /
``triage_table`` reporting view was dropped in the merge (ADR 0010, C2).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from algotrading.core.config import QcThresholdConfig
from algotrading.infra.contracts import QcResult
from algotrading.infra.forwards import ForwardEstimate
from algotrading.infra.qc import (
    ESCALATION_NONE,
    ESCALATION_NOTICE,
    ESCALATION_PAGE,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    build_report,
    check_calendar_sanity,
    check_collector_continuity,
    check_forward_stability,
    escalation_level,
    thresholds_from_config,
)
from algotrading.infra.surfaces import CalendarViolation

RUN_ID = "qc-run-2026-06-02"
RUN_TS = datetime(2026, 6, 2, 23, 30, tzinfo=UTC)

# Same concrete config as the checks suite, so the version stamp assertions match.
QC_CONFIG = QcThresholdConfig(
    version="qc-threshold-1.0.0",
    max_spread_pct=0.05,
    max_quote_age_seconds=30.0,
    min_chain_count=4,
)
THRESHOLDS = thresholds_from_config(QC_CONFIG)


@dataclasses.dataclass(frozen=True)
class _FakeSummary:
    """Minimal collector summary satisfying ``qc.CollectorContinuityInput``."""

    session_id: str
    gap_count: int
    subscribed_count: int
    covered_count: int


def _summary(*, session_id: str = "sess-1", gap_count: int = 0) -> _FakeSummary:
    return _FakeSummary(
        session_id=session_id, gap_count=gap_count, subscribed_count=100, covered_count=100
    )


def _forward(*, underlying: str, maturity: float, residual_mad: float) -> ForwardEstimate:
    return ForwardEstimate(
        underlying=underlying,
        maturity_years=maturity,
        forward=100.0,
        discount_factor=0.99,
        spot=100.0,
        implied_rate=0.01,
        implied_carry=0.0,
        implied_dividend=0.0,
        method="regression",
        reason_code="ok",
        quality_label="good",
        confidence=1.0,
        candidate_count=8,
        used_count=8,
        rejected_count=0,
        residual_mad=residual_mad,
        points=(),
    )


def _pass_collector() -> QcResult:
    return check_collector_continuity(
        _summary(gap_count=0), thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )


def _warn_collector() -> QcResult:
    # 3 gaps -> warn band (between warn_gap_count and max_gap_count).
    return check_collector_continuity(
        _summary(session_id="sess-warn", gap_count=3),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )


def _warning_severity_fail() -> QcResult:
    # forward stability fails with severity "warning": rel residual 2.0/100 = 0.02 > max_rel(0.01).
    return check_forward_stability(
        _forward(underlying="SX5E", maturity=0.5, residual_mad=2.0),
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )


def _critical_severity_fail() -> QcResult:
    # calendar sanity fails with severity "critical".
    violation = CalendarViolation(
        k=0.0, maturity_short=0.25, maturity_long=0.5, w_short=0.05, w_long=0.04
    )
    return check_calendar_sanity(
        [violation],
        "AAPL",
        thresholds=THRESHOLDS,
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )


def test_report_overall_status_is_worst_present() -> None:
    # one pass + one warn + one fail -> overall fail.
    report = build_report(
        [_pass_collector(), _warn_collector(), _warning_severity_fail()],
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    assert report.total == 3
    assert report.pass_count == 1
    assert report.warn_count == 1
    assert report.fail_count == 1
    assert report.overall_status == STATUS_FAIL


def test_report_warn_when_no_fail() -> None:
    report = build_report([_pass_collector(), _warn_collector()], run_id=RUN_ID, run_ts=RUN_TS)
    assert report.overall_status == STATUS_WARN
    assert not report.is_clean


def test_report_clean_when_all_pass() -> None:
    report = build_report([_pass_collector()], run_id=RUN_ID, run_ts=RUN_TS)
    assert report.overall_status == STATUS_PASS
    assert report.is_clean


def test_report_empty_is_clean_pass() -> None:
    # No checks run -> a clean pass report, not an invented failure.
    report = build_report([], run_id=RUN_ID, run_ts=RUN_TS)
    assert report.total == 0
    assert report.overall_status == STATUS_PASS
    assert report.is_clean


def test_escalation_page_on_critical_fail() -> None:
    report = build_report([_critical_severity_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    assert escalation_level(report) == ESCALATION_PAGE


def test_escalation_notice_on_warning_fail() -> None:
    report = build_report([_warning_severity_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    assert escalation_level(report) == ESCALATION_NOTICE


def test_escalation_notice_on_warn_only() -> None:
    report = build_report([_warn_collector()], run_id=RUN_ID, run_ts=RUN_TS)
    assert escalation_level(report) == ESCALATION_NOTICE


def test_escalation_none_on_clean() -> None:
    report = build_report([_pass_collector()], run_id=RUN_ID, run_ts=RUN_TS)
    assert escalation_level(report) == ESCALATION_NONE


def test_report_threshold_version_carried_on_rows() -> None:
    # Every emitted row points back at the config version that judged it.
    report = build_report([_warning_severity_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    assert all(r.threshold_version == QC_CONFIG.version for r in report.results)
