from __future__ import annotations

import os
from pathlib import Path

from .ports import ProfileRepository, RunRepository


def make_run_repository(
    *,
    sqlite_path: str | Path | None = None,
    postgres_dsn: str | None = None,
) -> RunRepository:
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
    from .sql_repositories import SqlProfileRepository, sqlite_engine_url

    return SqlProfileRepository(sqlite_engine_url(Path(sqlite_path)))
