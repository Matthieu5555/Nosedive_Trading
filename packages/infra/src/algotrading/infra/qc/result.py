"""The QC verdict vocabulary and the one builder for a ``QcResult``.

``contracts.QcResult.context`` is a single ``str`` field, not a mapping. That is a
deliberate storage shape (one queryable JSON blob per check), but a check still has
to *carry* structured specifics — the exact maturity, quote, underlying, or solver
that failed. So the context payload is built as a dict here and serialized to
canonical JSON (sorted keys, compact separators) by :func:`serialize_context`, the
same canonical-JSON discipline the platform config uses for its hashes. Canonical
ordering means two runs that disagree on dict insertion order still produce the
byte-identical context string, which keeps a stored QC result reproducible.

The whole point of the framework is specificity: a generic red banner is the
failure mode these checks exist to prevent. So every failing result names its
offending object in the context payload under an explicit key, and the tests assert
that exact name.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from algotrading.infra.contracts import QcResult

# Status: the three-way verdict every check returns. "pass" is healthy, "warn" is a
# soft breach worth an operator's eye but not an escalation, "fail" is a hard breach.
STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
QC_STATUSES: tuple[str, ...] = (STATUS_PASS, STATUS_WARN, STATUS_FAIL)

# Severity: how loud a failure is when it happens. A check declares its own severity
# (its blast radius), independent of whether it passed on this run.
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
QC_SEVERITIES: tuple[str, ...] = (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_CRITICAL)


def serialize_context(payload: dict[str, Any]) -> str:
    """Serialize a context payload to canonical JSON.

    Sorted keys and compact separators make the output a pure function of the
    payload's *contents*, not its construction order, so a stored QC context is
    reproducible across runs and processes.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def deserialize_context(context: str) -> dict[str, Any]:
    """Parse a context string produced by :func:`serialize_context` back to a dict.

    Provided so callers (and tests) read a QC result's specifics without re-parsing
    JSON by hand. The return is always a dict; a context that is not a JSON object is
    a corrupt result, so this lets the caller see the shape rather than guess.
    """
    parsed = json.loads(context)
    if not isinstance(parsed, dict):
        return {"raw": parsed}
    return parsed


def build_result(
    *,
    check_name: str,
    target_key: str,
    status: str,
    severity: str,
    measured_value: float,
    threshold_version: str,
    context: dict[str, Any],
    run_id: str,
    run_ts: datetime,
) -> QcResult:
    """Assemble one ``QcResult`` from a check's verdict and its named context.

    ``run_id`` and ``run_ts`` are injected by the caller, never read from a clock
    here, so a check is a pure function of its inputs and reproduces in replay.
    """
    return QcResult(
        run_id=run_id,
        check_name=check_name,
        target_key=target_key,
        run_ts=run_ts,
        qc_status=status,
        severity=severity,
        measured_value=measured_value,
        threshold_version=threshold_version,
        context=serialize_context(context),
    )
