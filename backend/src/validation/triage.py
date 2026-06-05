"""The unified triage layer: one record shape for both quality planes, one escalation.

Two planes flag problems with a day's run — the named QC checks (``src/qc``) and this
validation plane's anomaly detection. Before a reporting or alerting layer can act, those
two result shapes must collapse into one, or every consumer has to reconcile them. This
module is that collapse: it folds a :class:`~qc.report.QcReport` and a
:class:`~validation.engine.ValidationOutcome` into one worst-first list of
:class:`~contracts.TriageRecord` rows, and maps the list to one escalation level.

The merge preserves both planes' discipline. The specificity rule survives — a QC row's
headline is built by the *same* :func:`qc.result_headline` an operator already reads, and
its offender name by the same logic — so "surface fit failed for AMS 2026-09" does not
decay into "QC red" on its way into the unified table. The escalation rule is one policy
in one place: a critical-severity failure pages, any other failure or warning is a notice,
a clean run escalates to nothing — the same rule the QC plane already used, now spanning
both planes.

The records are pure values. Persisting them is the caller's job (the orchestration layer
writes them to the ``triage_records`` table through storage); this layer never does I/O.
"""

from __future__ import annotations

from collections.abc import Sequence

from contracts import QcResult, TriageRecord
from qc import (
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

from .engine import ValidationOutcome
from .state import ValidationStatus

SOURCE_QC = "qc"
SOURCE_VALIDATION = "validation"

# A validation/anomaly verdict carries no severity of its own (it is status-only), so the
# unified record derives one: a hard FAIL is critical (page-worthy), a WARN is a warning.
_VALIDATION_SEVERITY = {
    ValidationStatus.FAIL: SEVERITY_CRITICAL,
    ValidationStatus.WARN: SEVERITY_WARNING,
}

# Worst-first ordering, mirroring the QC plane's triage sort: fails before warns, then by
# severity, then a stable (source, name, target) tail so the order is total.
_SEVERITY_RANK = {SEVERITY_INFO: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}


def _qc_underlying(result: QcResult) -> str:
    """The underlying a QC row belongs to, for partitioning the triage table.

    Prefers the explicit ``underlying`` the check named in its context; otherwise takes
    the leading token of the target key (``"AAPL@0.5"`` / ``"AAPL|..."`` -> ``"AAPL"``);
    falls back to ``"_all"`` for run-wide targets (a collector session, a portfolio) that
    do not belong to a single underlying — the same fallback the storage layer uses.
    """
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
    """The reason code for a QC row: the one the check named, else the check itself."""
    context = deserialize_context(result.context)
    reason = context.get("reason_code")
    if isinstance(reason, str) and reason:
        return reason
    return result.check_name


def triage_from_qc(report: QcReport) -> tuple[TriageRecord, ...]:
    """Fold a QC report's non-passing rows into unified triage records (``source="qc"``)."""
    records = []
    for result in report.results:
        if result.status == STATUS_PASS:
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
                status=result.status,
                severity=result.severity,
                reason_code=_qc_reason(result),
                detail=result_headline(result),
                threshold_version=result.threshold_version,
            )
        )
    return tuple(records)


def triage_from_validation(outcome: ValidationOutcome) -> tuple[TriageRecord, ...]:
    """Fold a validation outcome's flags into unified records (``source="validation"``).

    Only non-passing checks become rows; a ``NO_BASELINE`` metric (a PASS — nothing to
    act on yet) is left out, so the triage list is things to investigate, not things to
    await.
    """
    report = outcome.report
    records = []
    for check in report.failures():
        records.append(
            TriageRecord(
                run_id=report.run_id,
                run_ts=report.as_of,
                underlying=report.underlying,
                source=SOURCE_VALIDATION,
                name=check.check,
                target_key=check.locator if check.locator is not None else check.check,
                status=check.status.value,
                severity=_VALIDATION_SEVERITY[check.status],
                # A non-PASS check always carries a reason_code (ValidationCheck enforces
                # it); the ``or`` only satisfies the type checker for the impossible None.
                reason_code=check.reason_code or check.check,
                detail=check.detail,
                threshold_version=report.threshold_version,
            )
        )
    return tuple(records)


def _sort_key(record: TriageRecord) -> tuple[int, int, str, str, str]:
    """Worst-first: fails before warns, then by severity, then a stable tie-break."""
    status_rank = 1 if record.status == STATUS_FAIL else 0
    severity_rank = _SEVERITY_RANK.get(record.severity, 0)
    return (-status_rank, -severity_rank, record.source, record.name, record.target_key)


def build_triage(
    *,
    qc_report: QcReport | None = None,
    validation: ValidationOutcome | None = None,
) -> tuple[TriageRecord, ...]:
    """Collapse both planes' results into one worst-first triage list.

    Either source may be absent (a run that produced only QC, or only validation). The
    output is deterministic: worst-first by status then severity, with a stable
    (source, name, target) tie-break, so the same inputs always yield the same table.
    """
    records: list[TriageRecord] = []
    if qc_report is not None:
        records.extend(triage_from_qc(qc_report))
    if validation is not None:
        records.extend(triage_from_validation(validation))
    records.sort(key=_sort_key)
    return tuple(records)


def escalation_level(records: Sequence[TriageRecord]) -> str:
    """Collapse a triage list to one escalation signal an alert layer thresholds on.

    A critical-severity failure pages; any other failure or any warning is a notice; an
    empty (clean) list escalates to nothing. One policy, spanning both planes.
    """
    has_critical_fail = any(
        r.status == STATUS_FAIL and r.severity == SEVERITY_CRITICAL for r in records
    )
    if has_critical_fail:
        return ESCALATION_PAGE
    if records:
        return ESCALATION_NOTICE
    return ESCALATION_NONE
