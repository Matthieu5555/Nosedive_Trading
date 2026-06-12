"""Factory: select the metadata-tier backend (engine URL) from environment configuration.

The backend choice is configuration, not code — callers depend on the ``RunRepository`` /
``ProfileRepository`` Protocols and never import a concrete store directly. Both ports are
served by one SQLAlchemy Core repository each (``sql_repositories.py``); "which backend"
is just which engine URL the factory builds. The selection rule for runs is:

    1. If ``POSTGRES_URL`` env var is set (or ``postgres_dsn`` is passed), connect to
       Postgres. Requires ``psycopg`` (``uv sync --extra postgres``).
    2. Otherwise use the SQLite file at ``sqlite_path``.
    3. If neither is available, raise ``ValueError``.

This is the ONLY place a caller should decide which backend to use. Do not construct
``SqlRunRepository`` or ``SqlProfileRepository`` directly in production code.

Example (orchestration bootstrap)::

    from algotrading.infra.storage.factory import make_run_repository
    import os

    runs = make_run_repository(sqlite_path=os.environ.get("RUNS_DB_PATH", "data/runs.db"))
    # If POSTGRES_URL is set in the environment, Postgres is used instead automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from .ports import ProfileRepository, RunRepository


def make_run_repository(
    *,
    sqlite_path: str | Path | None = None,
    postgres_dsn: str | None = None,
) -> RunRepository:
    """Return a ``RunRepository`` on the backend selected by configuration.

    Priority: Postgres (if ``POSTGRES_URL`` env var or ``postgres_dsn``) > SQLite.
    Raises ``ValueError`` if no backend can be configured.
    """
    from .sql_repositories import SqlRunRepository, postgres_engine_url, sqlite_engine_url

    dsn = postgres_dsn or os.environ.get("POSTGRES_URL")
    if dsn:
        return SqlRunRepository(postgres_engine_url(dsn))
    if sqlite_path is not None:
        return SqlRunRepository(sqlite_engine_url(Path(sqlite_path)))
    raise ValueError(
        "No run-registry backend configured. "
        "Set POSTGRES_URL for Postgres, or pass sqlite_path for SQLite."
    )


def make_profile_repository(*, sqlite_path: str | Path) -> ProfileRepository:
    """Return a ``ProfileRepository`` (SQLite — the config-profile metadata tier).

    The profile store is the SQLite "higher layer" of the storage direction (ADR 0028);
    pointing the same repository at a Postgres engine URL is a configuration change,
    not a new backend.
    """
    from .sql_repositories import SqlProfileRepository, sqlite_engine_url

    return SqlProfileRepository(sqlite_engine_url(Path(sqlite_path)))
