"""Thin orchestration wrapper that runs a job and records its lineage.

Wraps a callable so that, whatever the outcome, the run is recorded in the
:class:`RunRegistry` with a :class:`~algotrading.core.manifest.Manifest`: a ``run_id``
(idempotent — re-running under the same id overwrites, never duplicates), a
``correlation_id`` linking a collector session to the analytics it feeds, and the
final status. A failing job is recorded as failed and then re-raised — the failure is
observable, never swallowed. The job's own logic stays in the callable; this only
orchestrates.

This is the durable, manifest-keyed twin of :mod:`orchestration.run_state` (the EOD
stage ledger): ``run_state`` answers "which stage finished for which trade date" for the
restart/backlog logic, while this records the *lineage* of each job run — the
environment, code version, config hashes, input/output partitions, and correlation id —
into the M10 :class:`RunRegistry` so a run is auditable after the fact. It rides our
landed storage (ADR 0015 ``RunRepository`` tier); nothing here reads a wall clock unless
the default ``clock`` is used.
"""

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
    """The recorded run and the value the job returned."""

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
    run_id: str | None = None,
    correlation_id: str | None = None,
    input_partitions: Mapping[str, str] | None = None,
    output_partitions: Mapping[str, str] | None = None,
) -> RunResult[T]:
    """Run ``fn``, record the run (ok or failed) in ``registry``, and return its value.

    Pass an explicit ``run_id`` to make a restart idempotent (the same id overwrites its
    record). Pass ``input_partitions`` / ``output_partitions`` to record which data
    partitions the job consumed or wrote in its lineage manifest. A failing ``fn`` is
    recorded with status ``failed`` and the exception is re-raised. ``clock`` is injected
    so a deterministic caller (a test, a replay) supplies its own time and nothing here
    reads a wall clock; the default reads ``now`` for the live daily run.
    """
    started_at = clock()
    run_id = run_id or f"{name}-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}"
    correlation_id = correlation_id or uuid.uuid4().hex
    # Resolve the code identity once, at the entrypoint — never deeper in compute. Injected
    # for a deterministic caller (a test/replay supplies a fixed value); the default reads
    # git for the live run.
    resolved_code_identity = code_identity if code_identity is not None else _code_identity()

    def _record(status: str) -> RunRecord:
        manifest = Manifest(
            run_id=run_id,
            environment=environment,
            code_version=code_version,
            code_identity=resolved_code_identity,
            config_hashes=dict(config_hashes),
            input_partitions=dict(input_partitions or {}),
            output_partitions=dict(output_partitions or {}),
            status=status,
            correlation_id=correlation_id,
        )
        record = RunRecord(manifest, name, started_at, clock())
        registry.record(record)
        return record

    extra = {"job": name, "run_id": run_id, "correlation_id": correlation_id}
    # Catch broadly to record *any* job failure before re-raising — a generic runner
    # cannot know which exception types its callable raises. Nothing is swallowed: the
    # bare ``raise`` re-raises the original (and if recording itself fails, that error
    # carries the original as its context).
    try:
        value = fn()
    except Exception:  # noqa: BLE001 — record-then-reraise, see comment above
        _record(RunStatus.FAILED)
        _log.error("job failed", extra=extra)
        raise
    record = _record(RunStatus.OK)
    _log.info("job finished", extra=extra)
    return RunResult(record=record, value=value)
