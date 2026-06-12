"""Run registry: the durable record of which jobs ran, when, and whether they were healthy.

Every orchestrated job emits a :class:`~algotrading.core.manifest.Manifest` (its lineage);
this store persists one record per run so an operator can answer instantly: *what was the
last healthy run of this job?* and *what has run at all?* — the basis for backlog/staleness.

Records are keyed by ``run_id``, so re-running an idempotent job under the same id
overwrites its record rather than appending a duplicate. ``RunStatus`` is the small status
vocabulary that gives ``last_healthy`` a single, explicit definition of "healthy".

``RunRegistry`` is the file-based (JSON) zero-dependency reference implementation. The
preferred backend is ``SqlRunRepository`` (``sql_repositories.py``, SQLAlchemy Core) —
SQLite local/single-host or Postgres deployed/multi-host, chosen by engine URL. Use
``make_run_repository()`` from ``factory.py`` to select based on env.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from algotrading.core.manifest import Manifest
from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass


class RunStatus(StrEnum):
    """Outcome of a recorded run; ``last_healthy`` keys on ``OK``."""

    OK = "ok"
    FAILED = "failed"


@dataclass(frozen=True)
class RunRecord:
    """One job run: its lineage manifest, the job name, and the wall-clock window it ran in.

    A pydantic dataclass: construction validates/coerces (an ISO string becomes a
    ``datetime``, a manifest mapping becomes a :class:`Manifest` with the dataclass's own
    defaults applied), so deserialization never re-spells the ``Manifest`` constructor.
    ``to_dict`` stays hand-written because its byte shape is persisted (registry files,
    repository payload columns) and pinned by a golden test — pydantic's JSON dialect
    (``Z``-suffixed UTC datetimes) would silently move those bytes.
    """

    manifest: Manifest
    job: str
    started_at: datetime
    ended_at: datetime

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain JSON-ready dict (the shape written to the registry)."""
        return {
            "job": self.job,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "manifest": self.manifest.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunRecord:
        """Rebuild a record from its serialized form (validated, defaults applied)."""
        return _RUN_RECORD_ADAPTER.validate_python(payload)


_RUN_RECORD_ADAPTER = TypeAdapter(RunRecord)


class RunRegistry:
    """JSON-file registry of job runs, partitioned by job name.

    Layout: ``<root>/layer=runs/job=<J>/run_id=<id>.json``. Suitable for local dev and
    single-host deployments; use ``SqlRunRepository`` (SQLite or Postgres by engine URL)
    for indexed lookups and multi-host.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _job_dir(self, job: str) -> Path:
        return self.root / "layer=runs" / f"job={job}"

    def record(self, run: RunRecord) -> Path:
        """Persist one run record, keyed by ``run_id`` (idempotent: same id overwrites)."""
        job_dir = self._job_dir(run.job)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / f"run_id={run.manifest.run_id}.json"
        path.write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def list_runs(self, job: str) -> tuple[RunRecord, ...]:
        """All records for a job, oldest run first by end time, or ``()`` if none."""
        job_dir = self._job_dir(job)
        if not job_dir.exists():
            return ()
        records = [
            RunRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in job_dir.glob("run_id=*.json")
        ]
        return tuple(sorted(records, key=lambda r: r.ended_at))

    def last_healthy(self, job: str) -> RunRecord | None:
        """The most recent healthy (``status == RunStatus.OK``) run of a job, or ``None``."""
        healthy = [r for r in self.list_runs(job) if r.manifest.status == RunStatus.OK]
        return healthy[-1] if healthy else None
