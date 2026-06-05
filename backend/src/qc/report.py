"""The daily QC report, the triage table, and the escalation model.

The checks produce individual ``QcResult`` rows; this module rolls a day's rows
into one :class:`QcReport` (pass/warn/fail counts and the failing rows), turns the
failures into an operator-facing :class:`TriageTable` ordered worst-first, and maps
the report to one :class:`EscalationLevel` so an alerting layer has a single signal
to threshold on.

The whole design serves the spec's headline requirement: a daily operator finds the
failing underlyings/maturities within minutes. So the triage table leads with the
specific target and the named context, never a generic count.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from contracts import QcResult

from .result import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    deserialize_context,
)

# Escalation levels, lowest to highest. The daily report collapses to exactly one.
ESCALATION_NONE = "none"
ESCALATION_NOTICE = "notice"
ESCALATION_PAGE = "page"
ESCALATION_LEVELS: tuple[str, ...] = (ESCALATION_NONE, ESCALATION_NOTICE, ESCALATION_PAGE)

# How loud each severity ranks when triaging worst-first. Higher sorts earlier.
_SEVERITY_RANK = {SEVERITY_INFO: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}


@dataclass(frozen=True, slots=True)
class TriageRow:
    """One failing (or warning) check, ready for an operator to act on.

    Carries the named offending object pulled from the result's context, so the row
    is actionable on its own: which check, which target, how bad, and the specific
    name to investigate.
    """

    check_name: str
    target_key: str
    status: str
    severity: str
    measured_value: float
    threshold_version: str
    headline: str


@dataclass(frozen=True, slots=True)
class TriageTable:
    """The day's failing/warning rows, ordered worst-first for an operator."""

    rows: tuple[TriageRow, ...]


@dataclass(frozen=True, slots=True)
class QcReport:
    """The rolled-up daily QC outcome for one run.

    Holds the counts, the overall status, and the full set of result rows. The
    failing and warning rows are kept whole so the triage table and escalation are
    derived from them, not recomputed from a lossy summary.
    """

    run_id: str
    run_ts: datetime
    total: int
    pass_count: int
    warn_count: int
    fail_count: int
    overall_status: str
    results: tuple[QcResult, ...]

    @property
    def is_clean(self) -> bool:
        """True when no check warned or failed."""
        return self.fail_count == 0 and self.warn_count == 0


# The single field each check writes to name its offending object, in priority order.
# The first one present in a result's context becomes the triage headline's name.
_NAME_KEYS: tuple[str, ...] = (
    "failing_session",
    "failing_quote",
    "failing_contract",
    "failing_maturity",
    "failing_maturity_short",
    "missing_cells",
    "missing_contracts",
    "failing_solvers",
    "underlying",
    "metric",
    "target",
)


def _headline(result: QcResult) -> str:
    """Build a one-line, operator-facing headline naming the offending object.

    Reads the named keys the check wrote into the context. This is where "name the
    failing maturity/quote/solver" becomes the thing an operator actually reads.
    """
    context = deserialize_context(result.context)
    named: str | None = None
    for key in _NAME_KEYS:
        if key in context and context[key] not in ("", [], None):
            named = f"{key}={context[key]!r}"
            break
    measured = result.measured_value
    measured_text = f"{measured:g}" if math.isfinite(measured) else str(measured)
    where = f" [{named}]" if named is not None else ""
    return f"{result.check_name} {result.status} (measured={measured_text}){where}"


def build_report(
    results: Sequence[QcResult],
    *,
    run_id: str,
    run_ts: datetime,
) -> QcReport:
    """Roll a day's ``QcResult`` rows into one :class:`QcReport`.

    ``overall_status`` is the worst single status present: ``fail`` if any check
    failed, else ``warn`` if any warned, else ``pass``. An empty result set is a
    clean ``pass`` report (nothing checked, nothing wrong) — the report does not
    invent a failure from missing input.
    """
    counts = Counter(result.status for result in results)
    fail_count = counts.get(STATUS_FAIL, 0)
    warn_count = counts.get(STATUS_WARN, 0)
    pass_count = counts.get(STATUS_PASS, 0)
    if fail_count > 0:
        overall = STATUS_FAIL
    elif warn_count > 0:
        overall = STATUS_WARN
    else:
        overall = STATUS_PASS
    return QcReport(
        run_id=run_id,
        run_ts=run_ts,
        total=len(results),
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        overall_status=overall,
        results=tuple(results),
    )


def _triage_sort_key(row: TriageRow) -> tuple[int, int, float, str, str]:
    """Worst-first ordering: fails before warns, then by severity, then magnitude."""
    status_rank = 1 if row.status == STATUS_FAIL else 0
    severity_rank = _SEVERITY_RANK.get(row.severity, 0)
    magnitude = row.measured_value if math.isfinite(row.measured_value) else math.inf
    # Negative ranks so Python's ascending sort yields descending priority; the
    # check/target tail makes the order total and deterministic.
    return (-status_rank, -severity_rank, -magnitude, row.check_name, row.target_key)


def triage_table(report: QcReport) -> TriageTable:
    """Turn a report's non-passing rows into a worst-first :class:`TriageTable`.

    Passing rows are dropped (an operator triages problems, not health). The order is
    deterministic: fails before warns, then critical before warning before info, then
    larger measured magnitude first, then check name and target as a stable tie-break.
    """
    rows = [
        TriageRow(
            check_name=result.check_name,
            target_key=result.target_key,
            status=result.status,
            severity=result.severity,
            measured_value=result.measured_value,
            threshold_version=result.threshold_version,
            headline=_headline(result),
        )
        for result in report.results
        if result.status != STATUS_PASS
    ]
    rows.sort(key=_triage_sort_key)
    return TriageTable(rows=tuple(rows))


def escalation_level(report: QcReport) -> str:
    """Collapse a report to one escalation signal an alerting layer thresholds on.

    A critical-severity failure pages. Any other failure, or any warning, is a notice.
    A clean report escalates to nothing. This is the single rule the spec's "alerts
    for QC fails" hangs on, kept in one place so the policy cannot drift.
    """
    has_critical_fail = any(
        result.status == STATUS_FAIL and result.severity == SEVERITY_CRITICAL
        for result in report.results
    )
    if has_critical_fail:
        return ESCALATION_PAGE
    if report.fail_count > 0 or report.warn_count > 0:
        return ESCALATION_NOTICE
    return ESCALATION_NONE
