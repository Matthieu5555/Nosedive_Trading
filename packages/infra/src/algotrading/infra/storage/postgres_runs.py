"""PostgreSQL-backed run registry (M10 metadata/serving tier).

Satisfies the ``RunRepository`` port alongside ``SqliteRunRepository``; the choice of
backend is configuration, not code. Use ``make_run_repository()`` from ``factory.py``
to select via the ``POSTGRES_URL`` environment variable.

**Scope (M10):** the run registry only. The analytics data plane (raw events, derived
snapshots, forwards, IV, surfaces, risk) stays on Parquet/DuckDB and is never a Postgres
candidate — byte-identical replay depends on immutable columnar files (see ADR 0015).

**Dependency:** ``psycopg[binary]`` — declared as an optional extra in
``packages/infra/pyproject.toml``. Install with ``uv sync --extra postgres``.

**Schema:** one ``runs`` table, keyed by ``run_id``, indexed by ``(job, ended_at)``.
The ``payload`` column is ``JSONB`` so Postgres can filter/index individual run fields
in future operator queries without a schema change. The ``status`` column is a
denormalised copy of ``payload->>'manifest'->>'status'`` for fast ``last_healthy``
lookups without full JSON parsing.

**Idempotency:** ``ON CONFLICT (run_id) DO UPDATE`` — same semantics as SQLite's
``INSERT OR REPLACE``. Re-running a job under the same ``run_id`` overwrites its record.
"""

from __future__ import annotations

import json

from .runs import RunRecord, RunStatus

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id   TEXT        PRIMARY KEY,
    job      TEXT        NOT NULL,
    status   TEXT        NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    payload  JSONB       NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_job_ended ON runs (job, ended_at);
"""

_UPSERT = """
INSERT INTO runs (run_id, job, status, ended_at, payload)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (run_id) DO UPDATE
    SET job      = EXCLUDED.job,
        status   = EXCLUDED.status,
        ended_at = EXCLUDED.ended_at,
        payload  = EXCLUDED.payload
"""


class PostgresRunRepository:
    """Postgres registry of job runs; one row per ``run_id`` (re-run overwrites, idempotent).

    Satisfies the ``RunRepository`` port structurally — no inheritance required.
    Requires ``psycopg[binary]``: ``uv sync --extra postgres``.
    """

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg  # noqa: F401 — validated at construction time
        except ImportError as exc:
            raise ImportError(
                "PostgresRunRepository requires 'psycopg[binary]'. "
                "Install with: uv sync --extra postgres"
            ) from exc
        self._dsn = dsn
        self._init_schema()

    def _init_schema(self) -> None:
        import psycopg

        with psycopg.connect(self._dsn, autocommit=True) as con:
            con.execute(_DDL)

    def record(self, run: RunRecord) -> None:
        """Persist one run record, keyed by ``run_id`` (idempotent: same id overwrites)."""
        import psycopg

        with psycopg.connect(self._dsn) as con:
            con.execute(
                _UPSERT,
                (
                    run.manifest.run_id,
                    run.job,
                    str(run.manifest.status),
                    run.ended_at,
                    json.dumps(run.to_dict(), sort_keys=True),
                ),
            )

    def list_runs(self, job: str) -> tuple[RunRecord, ...]:
        """All records for a job, oldest run first by end time, or ``()`` if none."""
        import psycopg

        with psycopg.connect(self._dsn) as con:
            rows = con.execute(
                "SELECT payload FROM runs WHERE job = %s ORDER BY ended_at",
                (job,),
            ).fetchall()
        return tuple(RunRecord.from_dict(row[0]) for row in rows)

    def last_healthy(self, job: str) -> RunRecord | None:
        """The most recent healthy (``status == RunStatus.OK``) run of a job, or ``None``."""
        import psycopg

        with psycopg.connect(self._dsn) as con:
            row = con.execute(
                "SELECT payload FROM runs WHERE job = %s AND status = %s "
                "ORDER BY ended_at DESC LIMIT 1",
                (job, str(RunStatus.OK)),
            ).fetchone()
        return RunRecord.from_dict(row[0]) if row else None
