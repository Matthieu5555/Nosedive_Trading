"""SQLite-backed run registry (metadata tier): indexed point-lookups and last-healthy in SQL.

The lineage/registry tier is small, referential, and point-looked-up — a poor fit for
JSON-per-file and a clean fit for a single indexable SQLite file (trivial to back up,
single-file restore). This satisfies the ``RunRepository`` port alongside ``RunRegistry``;
it is metadata only and never on the deterministic reconstruction path. Records serialize
through the same ``RunRecord.to_dict`` so the on-the-wire shape is identical to the JSON
registry — a SQLite → JSON restore or vice-versa needs no schema migration.

Use ``PostgresRunRepository`` (same port) for multi-host or concurrent-write deployments.
Use ``make_run_repository()`` from ``factory.py`` to select the right backend by env var.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .runs import RunRecord, RunStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id   TEXT PRIMARY KEY,
    job      TEXT NOT NULL,
    status   TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    payload  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_job_ended ON runs (job, ended_at);
"""


class SqliteRunRepository:
    """SQLite registry of job runs; one row per ``run_id`` (re-run overwrites, idempotent).

    Satisfies the ``RunRepository`` port structurally — no inheritance required.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self._db_path)
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def record(self, run: RunRecord) -> None:
        """Persist one run record, keyed by ``run_id`` (idempotent: same id overwrites)."""
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO runs (run_id, job, status, ended_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    run.manifest.run_id,
                    run.job,
                    str(run.manifest.status),
                    run.ended_at.isoformat(),
                    json.dumps(run.to_dict(), sort_keys=True),
                ),
            )

    def list_runs(self, job: str) -> tuple[RunRecord, ...]:
        """All records for a job, oldest run first by end time, or ``()`` if none."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT payload FROM runs WHERE job = ? ORDER BY ended_at", (job,)
            ).fetchall()
        return tuple(RunRecord.from_dict(json.loads(row[0])) for row in rows)

    def last_healthy(self, job: str) -> RunRecord | None:
        """The most recent healthy (``status == RunStatus.OK``) run of a job, or ``None``."""
        with self._connect() as con:
            row = con.execute(
                "SELECT payload FROM runs WHERE job = ? AND status = ? "
                "ORDER BY ended_at DESC LIMIT 1",
                (job, str(RunStatus.OK)),
            ).fetchone()
        return RunRecord.from_dict(json.loads(row[0])) if row else None
