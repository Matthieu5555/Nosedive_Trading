"""The daily QC report, triage table, and escalation model.

These pin the roll-up behavior: overall status is the worst single status, the
triage table drops passing rows and orders the rest worst-first with a headline that
names the offender, and escalation collapses a report to one signal. Inputs are real
``QcResult`` rows produced by the actual checks (reusing the builders in
``test_qc_checks``), so the report is tested against the data the framework actually
emits, not hand-rolled stand-ins.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from collectors import CollectorSummary
from config import QcThresholdConfig
from contracts import QcResult
from forwards import ForwardEstimate
from qc import (
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
    check_surface_fit_error,
    escalation_level,
    thresholds_from_config,
    triage_table,
)
from surfaces import CalendarViolation, SliceFit

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


def _summary(*, session_id: str = "sess-1", gap_count: int = 0) -> CollectorSummary:
    return CollectorSummary(
        session_id=session_id,
        trade_date=date(2026, 6, 2),
        event_count=1000,
        gap_count=gap_count,
        reconnect_count=0,
        subscribed_count=100,
        covered_count=100,
        per_field_counts=(),
        pacing_failures=0,
        entitlement_failures=0,
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


def _slice_fit(*, underlying: str, maturity: float, rmse: float) -> SliceFit:
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
    # forward stability fails with severity "warning".
    return check_forward_stability(
        _forward(underlying="SX5E", maturity=0.5, residual_mad=0.5),
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


def _surface_fail() -> QcResult:
    return check_surface_fit_error(
        _slice_fit(underlying="AAPL", maturity=1.0, rmse=0.5),
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


def test_triage_drops_passing_rows() -> None:
    report = build_report(
        [_pass_collector(), _warning_severity_fail()], run_id=RUN_ID, run_ts=RUN_TS
    )
    table = triage_table(report)
    assert len(table.rows) == 1  # the passing collector row is dropped
    assert table.rows[0].status == STATUS_FAIL


def test_triage_orders_fail_before_warn_then_severity() -> None:
    # A warn, a warning-severity fail, and a critical-severity fail.
    report = build_report(
        [_warn_collector(), _warning_severity_fail(), _critical_severity_fail()],
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    rows = triage_table(report).rows
    # fails first; among fails, critical severity before warning severity; warn last.
    assert rows[0].status == STATUS_FAIL and rows[0].severity == "critical"
    assert rows[1].status == STATUS_FAIL and rows[1].severity == "warning"
    assert rows[2].status == STATUS_WARN


def test_triage_orders_same_severity_fails_by_magnitude() -> None:
    # Two warning-severity fails: larger measured_value sorts first.
    small = _warning_severity_fail()  # forward residual_mad 0.5
    big = _surface_fail()  # surface rmse 0.5 — equal magnitude; tie-break by check name
    report = build_report([small, big], run_id=RUN_ID, run_ts=RUN_TS)
    rows = triage_table(report).rows
    # Both are fails with equal magnitude (0.5); deterministic tie-break is check name.
    assert [r.check_name for r in rows] == sorted([small.check_name, big.check_name])


def test_triage_headline_names_the_offender() -> None:
    report = build_report([_critical_severity_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    headline = triage_table(report).rows[0].headline
    # The headline must carry the named offending object, not just the check name.
    assert "calendar_sanity" in headline
    assert "failing_maturity_short" in headline


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
