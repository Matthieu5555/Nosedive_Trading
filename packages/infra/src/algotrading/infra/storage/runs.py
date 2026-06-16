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

    OK = "ok"
    FAILED = "failed"


@dataclass(frozen=True)
class RunRecord:

    manifest: Manifest
    job: str
    started_at: datetime
    ended_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "job": self.job,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "manifest": self.manifest.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunRecord:
        return _RUN_RECORD_ADAPTER.validate_python(payload)


_RUN_RECORD_ADAPTER = TypeAdapter(RunRecord)


class RunRegistry:

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _job_dir(self, job: str) -> Path:
        return self.root / "layer=runs" / f"job={job}"

    def record(self, run: RunRecord) -> Path:
        job_dir = self._job_dir(run.job)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / f"run_id={run.manifest.run_id}.json"
        path.write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def list_runs(self, job: str) -> tuple[RunRecord, ...]:
        job_dir = self._job_dir(job)
        if not job_dir.exists():
            return ()
        records = [
            RunRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in job_dir.glob("run_id=*.json")
        ]
        return tuple(sorted(records, key=lambda r: r.ended_at))

    def last_healthy(self, job: str) -> RunRecord | None:
        healthy = [r for r in self.list_runs(job) if r.manifest.status == RunStatus.OK]
        return healthy[-1] if healthy else None
