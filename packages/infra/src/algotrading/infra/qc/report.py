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

ESCALATION_NONE = "none"
ESCALATION_NOTICE = "notice"
ESCALATION_PAGE = "page"
ESCALATION_LEVELS: tuple[str, ...] = (ESCALATION_NONE, ESCALATION_NOTICE, ESCALATION_PAGE)


@dataclass(frozen=True, slots=True)
class QcReport:

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
        return self.fail_count == 0 and self.warn_count == 0


_NAME_KEYS: tuple[str, ...] = (
    "failing_session",
    "failing_quote",
    "failing_contract",
    "failing_maturity",
    "failing_maturity_short",
    "missing_cells",
    "missing_contracts",
    "failing_solvers",
    "breaching_tenors",
    "band_gaps",
    "underlying",
    "metric",
    "target",
)


def named_offender(result: QcResult) -> str | None:
    context = deserialize_context(result.context)
    for key in _NAME_KEYS:
        if key in context and context[key] not in ("", [], None):
            return f"{key}={context[key]!r}"
    return None


def result_headline(result: QcResult) -> str:
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
    has_critical_fail = any(
        result.qc_status == STATUS_FAIL and result.severity == SEVERITY_CRITICAL
        for result in report.results
    )
    if has_critical_fail:
        return ESCALATION_PAGE
    if report.fail_count > 0 or report.warn_count > 0:
        return ESCALATION_NOTICE
    return ESCALATION_NONE
