from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from algotrading.core.log import get_logger
from algotrading.core.manifest import Manifest
from algotrading.core.provenance import code_identity as _code_identity
from algotrading.infra.storage import RunRecord, RunRegistry, RunStatus

_log = get_logger(__name__)


@dataclass(frozen=True)
class RunResult[T]:

    record: RunRecord
    value: T


def _now() -> datetime:
    return datetime.now(UTC)


def run_job[T](
    name: str,
    fn: Callable[[], T],
    *,
    registry: RunRegistry,
    environment: str,
    code_version: str,
    config_hashes: Mapping[str, str],
    clock: Callable[[], datetime] = _now,
    code_identity: str | None = None,
    config_snapshot: Mapping[str, object] | None = None,
    run_id: str | None = None,
    correlation_id: str | None = None,
    input_partitions: Mapping[str, str] | None = None,
    output_partitions: Mapping[str, str] | None = None,
) -> RunResult[T]:
    started_at = clock()
    run_id = run_id or f"{name}-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}"
    correlation_id = correlation_id or uuid.uuid4().hex
    resolved_code_identity = code_identity if code_identity is not None else _code_identity()

    def _record(status: str) -> RunRecord:
        manifest = Manifest(
            run_id=run_id,
            environment=environment,
            code_version=code_version,
            code_identity=resolved_code_identity,
            config_hashes=dict(config_hashes),
            config_snapshot=dict(config_snapshot or {}),
            input_partitions=dict(input_partitions or {}),
            output_partitions=dict(output_partitions or {}),
            status=status,
            correlation_id=correlation_id,
        )
        record = RunRecord(manifest, name, started_at, clock())
        registry.record(record)
        return record

    extra = {"job": name, "run_id": run_id, "correlation_id": correlation_id}
    try:
        value = fn()
    except Exception:  # noqa: BLE001 — record-then-reraise, see comment above
        _record(RunStatus.FAILED)
        _log.error("job failed", extra=extra)
        raise
    record = _record(RunStatus.OK)
    _log.info("job finished", extra=extra)
    return RunResult(record=record, value=value)
