"""The unified triage layer: both quality planes collapse into one shape and one rule.

Uses real Qc results from the actual checks (so the specificity that must survive the
merge is the specificity the checks really emit) and a real validation outcome, then
pins the cross-plane ordering, the single escalation policy, and round-trip persistence
of the unified record through the storage port (our "triage_store").
"""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from config import QcThresholdConfig
from contracts import ContractValidationError, QcResult, TriageRecord
from qc import (
    ESCALATION_NONE,
    ESCALATION_NOTICE,
    ESCALATION_PAGE,
    STATUS_FAIL,
    build_report,
    check_calendar_sanity,
    check_surface_fit_error,
    thresholds_from_config,
)
from storage import ParquetStore
from surfaces import CalendarViolation, SliceFit
from validation import (
    AnomalyThresholds,
    ValidationOutcome,
    build_triage,
    escalation_level,
    run_validation,
    triage_from_qc,
)

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


def _validation_fail() -> ValidationOutcome:
    # value 40 against the hand-scaled baseline -> robust z 5.5 -> FAIL (severity critical).
    return run_validation(
        run_id=RUN_ID,
        underlying="AAPL",
        as_of=RUN_TS,
        current_metrics={"n_iv_points": 40.0},
        baselines={"n_iv_points": BASELINE},
        thresholds=AnomalyThresholds(),
    )


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


def test_build_triage_combines_both_planes_worst_first() -> None:
    qc_report = build_report([_surface_fail(), _calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    records = build_triage(qc_report=qc_report, validation=_validation_fail())
    # Three failures across two planes, all in one list.
    assert len(records) == 3
    assert {r.source for r in records} == {"qc", "validation"}
    # Critical-severity fails sort ahead of the warning-severity one.
    rank = {"critical": 0, "warning": 1, "info": 2}
    severities = [r.severity for r in records]
    assert severities == sorted(severities, key=lambda s: rank[s])
    assert records[-1].severity == "warning"  # the surface fail lands last


def test_build_triage_is_deterministic() -> None:
    qc_report = build_report([_surface_fail(), _calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    a = build_triage(qc_report=qc_report, validation=_validation_fail())
    b = build_triage(qc_report=qc_report, validation=_validation_fail())
    assert a == b


def test_escalation_pages_on_critical_fail() -> None:
    records = build_triage(qc_report=build_report([_calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS))
    assert escalation_level(records) == ESCALATION_PAGE


def test_escalation_pages_on_validation_fail() -> None:
    # A validation FAIL is page-worthy even with no QC failure at all.
    records = build_triage(validation=_validation_fail())
    assert escalation_level(records) == ESCALATION_PAGE


def test_escalation_notice_on_warning_only() -> None:
    records = build_triage(qc_report=build_report([_surface_fail()], run_id=RUN_ID, run_ts=RUN_TS))
    assert escalation_level(records) == ESCALATION_NOTICE


def test_escalation_none_on_clean() -> None:
    clean = build_report([], run_id=RUN_ID, run_ts=RUN_TS)
    assert escalation_level(build_triage(qc_report=clean)) == ESCALATION_NONE


def test_triage_records_round_trip_through_storage() -> None:
    qc_report = build_report([_surface_fail(), _calendar_fail()], run_id=RUN_ID, run_ts=RUN_TS)
    records = build_triage(qc_report=qc_report, validation=_validation_fail())
    with tempfile.TemporaryDirectory() as tmp:
        store = ParquetStore(Path(tmp))
        store.write("triage_records", records)
        back = store.read("triage_records")
    assert len(back) == len(records)
    # Identity is the unified key; the set of (source, name, target) round-trips intact.
    assert {(r.source, r.name, r.target_key) for r in back} == {
        (r.source, r.name, r.target_key) for r in records
    }


def test_storage_rejects_a_malformed_triage_record() -> None:
    # The seam rule: a malformed instance is rejected by write-ahead validation, not
    # silently coerced. A naive (non-tz) run_ts is the malformed case here.
    bad = TriageRecord(
        run_id=RUN_ID,
        run_ts=datetime(2026, 6, 2, 23, 30),  # naive — no tzinfo
        underlying="AAPL",
        source="validation",
        name="n_iv_points",
        target_key="metric=n_iv_points",
        status="fail",
        severity="critical",
        reason_code="metric_anomaly",
        detail="robust z=5.5",
        threshold_version="v1",
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = ParquetStore(Path(tmp))
        with pytest.raises(ContractValidationError, match="timezone-aware"):
            store.write("triage_records", [bad])
