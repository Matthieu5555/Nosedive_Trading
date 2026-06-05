"""Factory: select the right ``RunRepository`` backend from environment configuration.

The backend choice is configuration, not code — callers depend on the ``RunRepository``
Protocol and never import a concrete store directly. The selection rule is:

    1. If ``POSTGRES_URL`` env var is set (or ``postgres_dsn`` is passed), return a
       ``PostgresRunRepository``. Requires ``psycopg[binary]`` (``uv sync --extra postgres``).
    2. Otherwise return a ``SqliteRunRepository`` at ``sqlite_path``.
    3. If neither is available, raise ``ValueError``.

This is the ONLY place a caller should decide which backend to use. Do not import
``SqliteRunRepository`` or ``PostgresRunRepository`` directly in production code.

Example (orchestration bootstrap)::

    from algotrading.infra.storage.factory import make_run_repository
    import os

    runs = make_run_repository(sqlite_path=os.environ.get("RUNS_DB_PATH", "data/runs.db"))
    # If POSTGRES_URL is set in the environment, Postgres is used instead automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from .ports import RunRepository


def make_run_repository(
    *,
    sqlite_path: str | Path | None = None,
    postgres_dsn: str | None = None,
) -> RunRepository:
    """Return a ``RunRepository`` backend selected by configuration.

    Priority: Postgres (if ``POSTGRES_URL`` env var or ``postgres_dsn``) > SQLite.
    Raises ``ValueError`` if no backend can be configured.
    """
    dsn = postgres_dsn or os.environ.get("POSTGRES_URL")
    if dsn:
        from .postgres_runs import PostgresRunRepository

        return PostgresRunRepository(dsn)

    if sqlite_path is not None:
        from .sqlite_runs import SqliteRunRepository

        return SqliteRunRepository(Path(sqlite_path))

    raise ValueError(
        "No run-registry backend configured. "
        "Set POSTGRES_URL for Postgres, or pass sqlite_path for SQLite."
    )
