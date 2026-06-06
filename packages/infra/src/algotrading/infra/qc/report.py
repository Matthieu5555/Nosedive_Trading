"""The daily QC report, the escalation model, and the offender-naming helpers.

The checks produce individual ``QcResult`` rows; this module rolls a day's rows
into one :class:`QcReport` (pass/warn/fail counts and the failing rows) and maps the
report to one :class:`EscalationLevel` so an alerting layer has a single signal to
threshold on. The offender-naming helpers (:func:`named_offender`,
:func:`result_headline`) read the specific failing object back out of a result's
context — the one place the specificity rule is read, so the unified triage layer
(:mod:`algotrading.infra.validation.triage`) names the *same* offender from the
*same* logic.

There is deliberately **no** in-memory triage-table shape here. The single persisted
triage shape both quality planes collapse into is ``contracts.TriageRecord`` (the
``triage_records`` table); a reporting view, if wanted, is derived from those records,
not from a parallel ``TriageRow`` (dropped in the merge — ADR 0010, C2).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from algotrading.infra.contracts import QcResult

from .result import (
    SEVERITY_CRITICAL,
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


@dataclass(frozen=True, slots=True)
class QcReport:
    """The rolled-up daily QC outcome for one run.

    Holds the counts, the overall status, and the full set of result rows. The
    failing and warning rows are kept whole so the unified triage records and
    escalation are derived from them, not recomputed from a lossy summary.
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


def named_offender(result: QcResult) -> str | None:
    """The ``key=value`` naming the offending object a check wrote into its context.

    Returns the first present, non-empty named key in priority order (the failing
    session, quote, contract, maturity, …), or ``None`` if the result named nothing.
    This is the one place the specificity rule is read back out, so the triage layer and
    the unified triage records both name the *same* offender from the *same* logic.
    """
    context = deserialize_context(result.context)
    for key in _NAME_KEYS:
        if key in context and context[key] not in ("", [], None):
            return f"{key}={context[key]!r}"
    return None


def result_headline(result: QcResult) -> str:
    """Build a one-line, operator-facing headline naming the offending object.

    Reads the named keys the check wrote into the context. This is where "name the
    failing maturity/quote/solver" becomes the thing an operator actually reads.
    """
    named = named_offender(result)
    measured = result.measured_value
    measured_text = f"{measured:g}" if math.isfinite(measured) else str(measured)
    where = f" [{named}]" if named is not None else ""
    return f"{result.check_name} {result.qc_status} (measured={measured_text}){where}"


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
    counts = Counter(result.qc_status for result in results)
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


def escalation_level(report: QcReport) -> str:
    """Collapse a QC report to one escalation signal an alerting layer thresholds on.

    A critical-severity failure pages. Any other failure, or any warning, is a notice.
    A clean report escalates to nothing. This is the QC plane's view of the same rule
    the unified triage layer applies across both planes
    (:func:`algotrading.infra.validation.triage.escalation_level`), kept in one place
    per plane so the policy cannot drift within it.
    """
    has_critical_fail = any(
        result.qc_status == STATUS_FAIL and result.severity == SEVERITY_CRITICAL
        for result in report.results
    )
    if has_critical_fail:
        return ESCALATION_PAGE
    if report.fail_count > 0 or report.warn_count > 0:
        return ESCALATION_NOTICE
    return ESCALATION_NONE
