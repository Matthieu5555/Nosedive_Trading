from __future__ import annotations

from collections.abc import Sequence

from algotrading.infra.contracts import QcResult, TriageRecord
from algotrading.infra.qc import (
    ESCALATION_NONE,
    ESCALATION_NOTICE,
    ESCALATION_PAGE,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    QcReport,
    deserialize_context,
    named_offender,
    result_headline,
)

from .engine import REASON_METRIC_ANOMALY, ValidationOutcome
from .state import ValidationCheck, ValidationStatus

SOURCE_QC = "qc"
SOURCE_VALIDATION = "validation"
SOURCE_ANOMALY = "anomaly"
SOURCES: tuple[str, ...] = (SOURCE_QC, SOURCE_VALIDATION, SOURCE_ANOMALY)

_VALIDATION_SEVERITY = {
    ValidationStatus.FAIL: SEVERITY_CRITICAL,
    ValidationStatus.WARN: SEVERITY_WARNING,
}

_SEVERITY_RANK = {SEVERITY_INFO: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}


def _qc_underlying(result: QcResult) -> str:
    context = deserialize_context(result.context)
    named = context.get("underlying")
    if isinstance(named, str) and named:
        return named
    target = result.target_key
    for sep in ("@", "|"):
        if sep in target:
            return target.split(sep, 1)[0]
    return target or "_all"


def _qc_reason(result: QcResult) -> str:
    context = deserialize_context(result.context)
    reason = context.get("reason_code")
    if isinstance(reason, str) and reason:
        return reason
    return result.check_name


def _validation_source(check: ValidationCheck) -> str:
    return SOURCE_ANOMALY if check.reason_code == REASON_METRIC_ANOMALY else SOURCE_VALIDATION


def triage_from_qc(report: QcReport) -> tuple[TriageRecord, ...]:
    records = []
    for result in report.results:
        if result.qc_status == STATUS_PASS:
            continue
        offender = named_offender(result)
        records.append(
            TriageRecord(
                run_id=result.run_id,
                run_ts=result.run_ts,
                underlying=_qc_underlying(result),
                source=SOURCE_QC,
                name=result.check_name,
                target_key=offender if offender is not None else result.target_key,
                status=result.qc_status,
                severity=result.severity,
                reason_code=_qc_reason(result),
                detail=result_headline(result),
                threshold_version=result.threshold_version,
            )
        )
    return tuple(records)


def triage_from_validation(outcome: ValidationOutcome) -> tuple[TriageRecord, ...]:
    report = outcome.report
    records = []
    for check in report.failures():
        records.append(
            TriageRecord(
                run_id=report.run_id,
                run_ts=report.as_of,
                underlying=report.underlying,
                source=_validation_source(check),
                name=check.check,
                target_key=check.locator if check.locator is not None else check.check,
                status=check.status.value,
                severity=_VALIDATION_SEVERITY[check.status],
                reason_code=check.reason_code or check.check,
                detail=check.detail,
                threshold_version=report.threshold_version,
            )
        )
    return tuple(records)


def _sort_key(record: TriageRecord) -> tuple[int, int, str, str, str]:
    status_rank = 1 if record.status == STATUS_FAIL else 0
    severity_rank = _SEVERITY_RANK.get(record.severity, 0)
    return (-status_rank, -severity_rank, record.source, record.name, record.target_key)


def build_triage(
    *,
    qc_report: QcReport | None = None,
    validation: ValidationOutcome | None = None,
) -> tuple[TriageRecord, ...]:
    records: list[TriageRecord] = []
    if qc_report is not None:
        records.extend(triage_from_qc(qc_report))
    if validation is not None:
        records.extend(triage_from_validation(validation))
    records.sort(key=_sort_key)
    return tuple(records)


def escalation_level(records: Sequence[TriageRecord]) -> str:
    has_critical_fail = any(
        r.status == STATUS_FAIL and r.severity == SEVERITY_CRITICAL for r in records
    )
    if has_critical_fail:
        return ESCALATION_PAGE
    if records:
        return ESCALATION_NOTICE
    return ESCALATION_NONE
