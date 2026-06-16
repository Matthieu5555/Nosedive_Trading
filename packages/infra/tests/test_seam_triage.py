from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.config import QcThresholdConfig
from algotrading.infra.contracts import ContractValidationError, TriageRecord
from algotrading.infra.qc import (
    build_report,
    check_calendar_sanity,
    thresholds_from_config,
)
from algotrading.infra.storage import ParquetStore
from algotrading.infra.surfaces import CalendarViolation
from algotrading.infra.validation import (
    AnomalyThresholds,
    ValidationOutcome,
    ValidationReport,
    ValidationStatus,
    build_triage,
    run_validation,
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


def _qc_records() -> tuple[TriageRecord, ...]:
    violation = CalendarViolation(
        k=0.0, maturity_short=0.25, maturity_long=0.5, w_short=0.05, w_long=0.04
    )
    qc_report = build_report(
        [check_calendar_sanity([violation], "AAPL", thresholds=THRESHOLDS, run_id=RUN_ID, run_ts=RUN_TS)],
        run_id=RUN_ID,
        run_ts=RUN_TS,
    )
    return build_triage(qc_report=qc_report)


def _anomaly_records() -> tuple[TriageRecord, ...]:
    outcome = run_validation(
        run_id=RUN_ID,
        underlying="MSFT",
        as_of=RUN_TS,
        current_metrics={"n_iv_points": 40.0},
        baselines={"n_iv_points": BASELINE},
        thresholds=AnomalyThresholds(),
    )
    return triage_from_validation(outcome)


def _validation_records() -> tuple[TriageRecord, ...]:
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
        underlying="NVDA",
        as_of=RUN_TS,
        checks=(check,),
        threshold_version="val-1.0.0",
    )
    return triage_from_validation(ValidationOutcome(report=report, anomalies=()))


def test_three_sources_round_trip_through_storage(tmp_path: Path) -> None:
    records = (*_qc_records(), *_validation_records(), *_anomaly_records())
    assert {r.source for r in records} == {"qc", "validation", "anomaly"}

    store = ParquetStore(tmp_path)
    store.write("triage_records", records)
    back = store.read("triage_records")

    assert len(back) == len(records)
    assert {(r.source, r.name, r.target_key) for r in back} == {
        (r.source, r.name, r.target_key) for r in records
    }
    assert {r.source for r in back} == {"qc", "validation", "anomaly"}


def test_single_record_round_trips_equal(tmp_path: Path) -> None:
    (record,) = _validation_records()
    store = ParquetStore(tmp_path)
    store.write("triage_records", [record])
    assert store.read("triage_records") == [record]


def test_storage_rejects_a_malformed_triage_record(tmp_path: Path) -> None:
    bad = TriageRecord(
        run_id=RUN_ID,
        run_ts=datetime(2026, 6, 2, 23, 30),
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
    store = ParquetStore(tmp_path)
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        store.write("triage_records", [bad])


def test_missing_primary_key_field_is_rejected(tmp_path: Path) -> None:
    bad = TriageRecord(
        run_id=RUN_ID,
        run_ts=RUN_TS,
        underlying="AAPL",
        source=None,  # type: ignore[arg-type]
        name="n_iv_points",
        target_key="metric=n_iv_points",
        status="fail",
        severity="critical",
        reason_code="metric_anomaly",
        detail="robust z=5.5",
        threshold_version="v1",
    )
    store = ParquetStore(tmp_path)
    with pytest.raises(ContractValidationError):
        store.write("triage_records", [bad])


def _grid_breach_qc_report() -> tuple[TriageRecord, ...]:

    from algotrading.core.config import GridQcConfig
    from algotrading.infra.qc import check_tenor_coverage_floor

    grid_cfg = GridQcConfig(
        version="grid-qc-seam",
        tenor_floors={"10d": 2, "1m": 2, "3m": 2},
        band_low_delta=-0.30,
        band_high_delta=0.30,
        max_delta_step=0.35,
    )
    thresholds = thresholds_from_config(QC_CONFIG.model_copy(update={"grid": grid_cfg}))

    class _GP:
        def __init__(self, underlying: str, tenor: str, delta: float) -> None:
            self.underlying = underlying
            self.tenor_label = tenor
            self.delta = delta

    # 10d and 3m are liquid (clear floor 2) so [10d, 3m] is the liquid range; 1m sits strictly
    # inside it with a partial 1/2 capture — a within-liquid-range CRITICAL collapse (ADR 0052),
    # which lands in triage and names the offending tenor.
    points = [_GP("SPX", "10d", d) for d in (-0.3, 0.3)]
    points += [_GP("SPX", "3m", d) for d in (-0.3, 0.3)]
    points += [_GP("SPX", "1m", -0.3)]
    result = check_tenor_coverage_floor(
        points, "SPX", ("10d", "1m", "3m"),
        thresholds=thresholds, run_id=RUN_ID, run_ts=RUN_TS,
    )
    report = build_report([result], run_id=RUN_ID, run_ts=RUN_TS)
    return build_triage(qc_report=report)


def test_grid_breach_lands_in_triage_records(tmp_path: Path) -> None:
    records = _grid_breach_qc_report()
    assert len(records) == 1
    (record,) = records
    assert record.source == "qc"
    assert record.name == "tenor_coverage_floor"
    assert record.underlying == "SPX"
    assert "1m" in record.detail

    store = ParquetStore(tmp_path)
    store.write("triage_records", records)
    assert store.read("triage_records") == list(records)


def test_grid_breach_malformed_triage_record_rejected(tmp_path: Path) -> None:
    (good,) = _grid_breach_qc_report()
    bad = TriageRecord(
        run_id=good.run_id,
        run_ts=datetime(2026, 6, 2, 23, 30),
        underlying=good.underlying,
        source=good.source,
        name=good.name,
        target_key=good.target_key,
        status=good.status,
        severity=good.severity,
        reason_code=good.reason_code,
        detail=good.detail,
        threshold_version=good.threshold_version,
    )
    store = ParquetStore(tmp_path)
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        store.write("triage_records", [bad])


def test_triage_date_partitioning_groups_by_underlying(tmp_path: Path) -> None:
    records = (*_qc_records(), *_validation_records(), *_anomaly_records())
    store = ParquetStore(tmp_path)
    store.write("triage_records", records)
    parts = store.list_partitions("triage_records")
    underlyings = {u for _, u in parts}
    assert {"AAPL", "MSFT", "NVDA"} <= underlyings
    assert all(isinstance(d, date) for d, _ in parts)
