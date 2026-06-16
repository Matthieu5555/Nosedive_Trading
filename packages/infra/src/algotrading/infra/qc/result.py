from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from algotrading.infra.contracts import QcResult

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
QC_STATUSES: tuple[str, ...] = (STATUS_PASS, STATUS_WARN, STATUS_FAIL)

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
QC_SEVERITIES: tuple[str, ...] = (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_CRITICAL)


def serialize_context(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def deserialize_context(context: str) -> dict[str, Any]:
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
