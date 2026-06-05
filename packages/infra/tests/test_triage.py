"""The unified triage layer: both quality planes collapse into one shape and one rule.

Uses real ``QcResult`` rows from the actual checks (so the specificity that must survive
the merge is the specificity the checks really emit) and a real validation outcome, then
pins the three-source discriminant (``qc`` / ``validation`` / ``anomaly``), the cross-
plane worst-first ordering, the offender-naming headline, the single escalation policy,
and the determinism a stored table depends on.

Persistence of the unified record through the storage port lives in ``test_seam_triage.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from algotrading.core.config import QcThresholdConfig
from algotrading.infra.contracts import QcResult
from algotrading.infra.qc import (
    ESCALATION_NONE,
    ESCALATION_NOTICE,
    ESCALATION_PAGE,
    STATUS_FAIL,
    build_report,
    check_calendar_sanity,
    check_surface_fit_error,
    thresholds_from_config,
)
from algotrading.infra.surfaces import CalendarViolation, SliceFit
from algotrading.infra.validation import (
    AnomalyThresholds,
    ValidationOutcome,
    ValidationReport,
    ValidationStatus,
    build_triage,
    escalation_level,
    run_validation,
    triage_from_qc,
    triage_from_validation,
)
from algotrading.infra.validation.state import ValidationCheck

RUN_ID = "run-2026-06-02"
RUN_TS = datetime(2026, 6, 2, 23, 30, tzinfo=UTC)
QC_CONFIG = QcThresholdConfig(
    version="qc-threshold-1.0.0",
    max_spread_pct=0.05,
    max_quote_age_seconds=30.0,
    min_chain_count=4,
)
THRESHOLDS = thresholds_from_config(QC_CONFIG)
BASELINE = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0]


def _surface_fail() -> QcResult:
    # A surface-fit fail carries severity "warning" and names the failing maturity.
    fit = SliceFit(
        underlying="AAPL",
        maturity_years=1.0,
        expiry_date=date(2026, 9, 1),
        day_count="ACT/365",
        method="svi",
        svi=None,
        rmse=0.5,
        n_points=10,
        arb_free=True,
        bound_hits=(),
        butterfly_violations=(),
        nonparametric_ks=(),
        nonparametric_ws=(),
        raw_points=(),
    )
    return check_surface_fit_error(fit, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)


def _calendar_fail() -> QcResult:
    # A calendar-sanity fail carries severity "critical".
    violation = CalendarViolation(
        k=0.0, maturity_short=0.25, maturity_long=0.5, w_short=0.05, w_long=0.04
    )
    return check_calendar_sanity(
        [violation], "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS
    )


def _anomaly_fail() -> ValidationOutcome:
    # value 40 against the hand-scaled baseline -> robust z 5.5 -> FAIL. A metric-anomaly
    # flag, so its triage source is "anomaly".
    return run_validation(
        run_id=RUN_ID,
        underlying="AAPL",
        as_of=RUN_TS,
        current_metrics={"n_iv_points": 40.0},
        baselines={"n_iv_points": BASELINE},
        thresholds=AnomalyThresholds(),
    )


def _validation_fail() -> ValidationOutcome:
    # A non-anomaly validation check (a structural/cross-field flag): its reason_code is
    # NOT metric_anomaly, so its triage source is "validation", not "anomaly".
    check = ValidationCheck(
        check="schema_consistency",
        status=ValidationStatus.FAIL,
        detail="iv_points missing source_snapshot_ts",
        locator="table=iv_points",
        reason_code="schema_mismatch",
        measured=None,
    )
    report = ValidationReport.from_checks(
        run_id=RUN_ID,
        underlying="AAPL",
        as_of=RUN_TS,
        checks=(check,),
        threshold_version="val-1.0.0",
    )
    return ValidationOutcome(report=report, anomalies=())


def test_triage_from_qc_drops_passes_and_names_the_offender() -> None:
    # A passing surface fit alongside a failing one: only the failure becomes a row.
    good = SliceFit(
        underlying="AAPL",
        maturity_years=0.5,
        expiry_date=date(2026, 9, 1),
        day_count="ACT/365",
        method="svi",
        svi=None,
        rmse=0.0001,
        n_points=10,
        arb_free=True,
        bound_hits=(),
        butterfly_violations=(),
        nonparametric_ks=(),
        nonparametric_ws=(),
        raw_points=(),
    )
    passing = check_surface_fit_error(good, thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)
    report = build_report([passing, _surface_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    records = triage_from_qc(report)
    assert len(records) == 1
    row = records[0]
    assert row.source == "qc"
    assert row.status == STATUS_FAIL
    # The merge must not lose the named offender: the failing maturity is in the row.
    assert "failing_maturity" in row.target_key
    assert "surface_fit_error" in row.detail
    # The underlying is recovered for partitioning, from the "AAPL@1" target key.
    assert row.underlying == "AAPL"


def test_anomaly_flag_lands_under_source_anomaly() -> None:
    # The rolling-baseline plane: a metric_anomaly flag is discriminated as "anomaly".
    records = triage_from_validation(_anomaly_fail())
    assert len(records) == 1
    assert records[0].source == "anomaly"
    assert records[0].reason_code == "metric_anomaly"
    assert records[0].status == STATUS_FAIL


def test_non_anomaly_validation_flag_lands_under_source_validation() -> None:
    # A validation check whose reason is not metric_anomaly is the "validation" source.
    records = triage_from_validation(_validation_fail())
    assert len(records) == 1
    assert records[0].source == "validation"
    assert records[0].reason_code == "schema_mismatch"


def test_build_triage_carries_all_three_sources_with_one_shape() -> None:
    # The headline collapse: qc + validation + anomaly all land in one list of one shape,
    # each with the correct source discriminant. (C2 test surface.)
    qc_report = build_report([_calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    records = build_triage(qc_report=qc_report, validation=_anomaly_fail())
    # The anomaly outcome only carries an anomaly row; add the validation-source one too.
    records = (*records, *triage_from_validation(_validation_fail()))
    sources = {r.source for r in records}
    assert sources == {"qc", "validation", "anomaly"}
    # One persisted shape: every row is the same TriageRecord, exposing one identical
    # column set — no second shape for a reporting layer to reconcile.
    expected_cols = (
        "detail",
        "name",
        "reason_code",
        "run_id",
        "run_ts",
        "severity",
        "source",
        "status",
        "target_key",
        "threshold_version",
        "underlying",
    )
    for r in records:
        assert tuple(sorted(r.__slots__)) == expected_cols


def test_build_triage_combines_planes_worst_first() -> None:
    qc_report = build_report([_surface_fail(), _calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    records = build_triage(qc_report=qc_report, validation=_anomaly_fail())
    # Three failures across two planes, all in one list.
    assert len(records) == 3
    assert {r.source for r in records} == {"qc", "anomaly"}
    # Critical-severity fails sort ahead of the warning-severity one.
    rank = {"critical": 0, "warning": 1, "info": 2}
    severities = [r.severity for r in records]
    assert severities == sorted(severities, key=lambda s: rank[s])
    assert records[-1].severity == "warning"  # the surface fail lands last


def test_build_triage_is_deterministic() -> None:
    qc_report = build_report([_surface_fail(), _calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    a = build_triage(qc_report=qc_report, validation=_anomaly_fail())
    b = build_triage(qc_report=qc_report, validation=_anomaly_fail())
    assert a == b


def test_escalation_pages_on_critical_fail() -> None:
    records = build_triage(qc_report=build_report([_calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS))
    assert escalation_level(records) == ESCALATION_PAGE


def test_escalation_pages_on_anomaly_fail() -> None:
    # An anomaly FAIL is page-worthy even with no QC failure at all.
    records = build_triage(validation=_anomaly_fail())
    assert escalation_level(records) == ESCALATION_PAGE


def test_escalation_notice_on_warning_only() -> None:
    records = build_triage(qc_report=build_report([_surface_fail()], run_id=RUN_ID, run_ts=RUN_TS))
    assert escalation_level(records) == ESCALATION_NOTICE


def test_escalation_none_on_clean() -> None:
    clean = build_report([], run_id=RUN_ID, run_ts=RUN_TS)
    assert escalation_level(build_triage(qc_report=clean)) == ESCALATION_NONE
