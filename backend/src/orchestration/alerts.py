"""Named alert conditions with documented detection intervals, on an injected clock.

An alert here is a named, pure condition over recorded state plus the current time:
given what the operator can see (the run-state ledger, a collector's last heartbeat, a
QC report, the partitions on disk) and an injected ``now``, is this condition firing?
Keeping each condition a pure function of (state, now) is what makes the
"detected within N" guarantee testable with an injected clock and no real wait: the
test advances a :class:`connectivity.ManualClock` to ``last_seen + interval + epsilon``
and asserts the alert fires, and to just inside the interval and asserts it does not.

Each condition has a *detection interval*: the bound within which the orchestration
layer promises to notice it. The interval is the contract the test pins, and it is
documented here and in the README. The four conditions the spec names:

* **collector death** — no observation has been recorded for a session within
  ``collector_silence_seconds``. Detected within that interval of the last heartbeat.
* **missing partition** — an expected ``(trade_date, underlying)`` analytic partition
  is absent. Detected on the next evaluation (an absence is immediate, not timed); it
  is never masked by interpolation — the alert names the missing partition.
* **elevated failure rate** — the share of failing stage runs over a recent window
  exceeds ``max_failure_ratio``. Detected on the next evaluation after the window
  fills.
* **QC fail** — the day's QC report escalated to ``page`` (a critical-severity QC
  failure). Detected the moment the QC job's report is evaluated.

Evaluation returns the firing alerts, each naming its subject, so an operator reads
*what* fired, not just *that* something did.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from qc import ESCALATION_PAGE, QcReport, escalation_level

from .run_state import OUTCOME_OK, StageRun

# Alert kind constants — the stable name each condition fires under.
ALERT_COLLECTOR_DEATH = "collector_death"
ALERT_MISSING_PARTITION = "missing_partition"
ALERT_ELEVATED_FAILURE_RATE = "elevated_failure_rate"
ALERT_QC_FAIL = "qc_fail"

# Documented detection intervals (seconds). These are the bounds the orchestration
# layer promises to notice a condition within; they are the contract the timing tests
# pin. They sit at the top of the file, each with the impact of changing it.
# Collector death: a live feed should produce *something* within this window; silence
# longer than a subscription's slowest expected cadence means the collector is dead.
COLLECTOR_SILENCE_SECONDS = 120.0
# Elevated failure rate: the share of failed stage runs over the recent window above
# which we alert. Half the recent runs failing is a systemic problem, not noise.
MAX_FAILURE_RATIO = 0.5
# Elevated failure rate: how many recent stage runs form the window the ratio is over.
FAILURE_WINDOW = 6


@dataclass(frozen=True, slots=True)
class Alert:
    """One firing alert: which condition, what it is about, and a readable message."""

    kind: str
    subject: str
    detail: str
    detection_interval_seconds: float


def collector_death_alert(
    *,
    session_id: str,
    last_event_ts: datetime | None,
    now: datetime,
    silence_seconds: float = COLLECTOR_SILENCE_SECONDS,
) -> Alert | None:
    """Fire when a session has produced no observation within the silence window.

    ``last_event_ts`` is the receipt time of the session's most recent observation (its
    heartbeat); ``now`` is the injected current time. The alert fires when the gap
    reaches ``silence_seconds`` — so it is detected within that interval of the last
    heartbeat. A session that has never produced an event (``last_event_ts is None``)
    fires immediately: a live session with no first tick is already wrong. Returns
    ``None`` when the session is alive.
    """
    if last_event_ts is None:
        return Alert(
            kind=ALERT_COLLECTOR_DEATH,
            subject=session_id,
            detail="no observation ever recorded for session",
            detection_interval_seconds=silence_seconds,
        )
    silent_for = (now - last_event_ts).total_seconds()
    if silent_for >= silence_seconds:
        return Alert(
            kind=ALERT_COLLECTOR_DEATH,
            subject=session_id,
            detail=f"silent for {silent_for:g}s (>= {silence_seconds:g}s)",
            detection_interval_seconds=silence_seconds,
        )
    return None


def missing_partition_alerts(
    *,
    table: str,
    expected: Sequence[tuple[date, str]],
    present: Sequence[tuple[date, str]],
) -> list[Alert]:
    """Fire one alert per expected analytic partition that is absent on disk.

    A missing partition is flagged explicitly and named (``table trade_date/underlying``)
    — never masked by silent interpolation, which is the failure mode this check exists
    to prevent. ``expected`` and ``present`` are ``(trade_date, underlying)`` pairs;
    ``present`` comes from ``store.list_partitions(table)``. Detection is immediate (an
    absence needs no timer), so this takes no clock. Returns one alert per gap, ordered.
    """
    present_set = set(present)
    alerts: list[Alert] = []
    for trade_date, underlying in sorted(set(expected) - present_set):
        alerts.append(
            Alert(
                kind=ALERT_MISSING_PARTITION,
                subject=f"{table} {trade_date.isoformat()}/{underlying}",
                detail="expected analytic partition absent — not interpolated",
                detection_interval_seconds=0.0,
            )
        )
    return alerts


def elevated_failure_rate_alert(
    *,
    runs: Sequence[StageRun],
    window: int = FAILURE_WINDOW,
    max_failure_ratio: float = MAX_FAILURE_RATIO,
) -> Alert | None:
    """Fire when the recent stage runs failed at a rate above ``max_failure_ratio``.

    Looks at the most recent ``window`` recorded stage runs (any stage, any date) and
    fires when the fraction that did not finish cleanly exceeds the threshold. Fewer
    than ``window`` runs recorded means there is not yet enough history to judge a
    *rate*, so it does not fire (one early failure is not an elevated rate). Returns
    ``None`` when the recent failure rate is acceptable.
    """
    if len(runs) < window:
        return None
    recent = list(runs)[-window:]
    failed = sum(1 for run in recent if run.outcome != OUTCOME_OK)
    ratio = failed / window
    if ratio > max_failure_ratio:
        return Alert(
            kind=ALERT_ELEVATED_FAILURE_RATE,
            subject=f"last {window} stage runs",
            detail=f"failure ratio {ratio:g} (> {max_failure_ratio:g})",
            detection_interval_seconds=0.0,
        )
    return None


def qc_fail_alert(report: QcReport) -> Alert | None:
    """Fire when the day's QC report escalates to a page (a critical-severity fail).

    Reuses the QC plane's own escalation rule (``escalation_level``) so the "alert on
    QC fails" policy has exactly one definition and cannot drift from the report's. A
    ``page`` escalation fires; a notice or a clean report does not (a warn is for the
    triage queue, not a page). Detected the moment the report is evaluated.
    """
    if escalation_level(report) == ESCALATION_PAGE:
        return Alert(
            kind=ALERT_QC_FAIL,
            subject=report.run_id,
            detail=f"QC report escalated to page ({report.fail_count} fail(s))",
            detection_interval_seconds=0.0,
        )
    return None
