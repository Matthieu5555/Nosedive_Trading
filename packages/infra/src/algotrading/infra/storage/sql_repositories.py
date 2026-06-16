from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from .profiles import ProfileVersion
from .runs import RunRecord, RunStatus


class IsoDateTimeText(sa.types.TypeDecorator[datetime]):

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect: sa.Dialect) -> sa.types.TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.Text())
        return dialect.type_descriptor(sa.DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: sa.Dialect) -> Any:
        if value is None or dialect.name != "sqlite":
            return value
        return value.isoformat()

    def process_result_value(self, value: Any, dialect: sa.Dialect) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)


class CanonicalJsonDocument(sa.types.TypeDecorator[dict[str, Any]]):

    impl = sa.Text()
    cache_ok = True

    def load_dialect_impl(self, dialect: sa.Dialect) -> sa.types.TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(sa.Text())

    def process_bind_param(self, value: dict[str, Any] | None, dialect: sa.Dialect) -> Any:
        if value is None or dialect.name == "postgresql":
            return value
        return json.dumps(value, sort_keys=True)

    def process_result_value(self, value: Any, dialect: sa.Dialect) -> dict[str, Any] | None:
        if isinstance(value, str):
            return json.loads(value)
        return value


_METADATA = sa.MetaData()

_RUNS = sa.Table(
    "runs",
    _METADATA,
    sa.Column("run_id", sa.Text, primary_key=True),
    sa.Column("job", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("ended_at", IsoDateTimeText(), nullable=False),
    sa.Column("payload", CanonicalJsonDocument(), nullable=False),
    sa.Index("idx_runs_job_ended", "job", "ended_at"),
)

_PROFILES = sa.Table(
    "profiles",
    _METADATA,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("content_hash", sa.Text, primary_key=True),
    sa.Column("effective_from", sa.Date, nullable=False),
    sa.Column("payload", CanonicalJsonDocument(), nullable=False),
    sa.Index("idx_profiles_name_eff", "name", "effective_from"),
)


def sqlite_engine_url(db_path: Path) -> sa.URL:
    return sa.URL.create("sqlite", database=str(db_path))


def postgres_engine_url(dsn: str) -> str:
    scheme, separator, rest = dsn.partition("://")
    if separator and scheme in ("postgres", "postgresql"):
        return f"postgresql+psycopg://{rest}"
    return dsn


def _engine_for(url: str | sa.URL) -> sa.Engine:
    url_object = sa.make_url(url)
    database = url_object.database
    if url_object.get_backend_name() == "sqlite" and database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    try:
        return sa.create_engine(url_object)
    except ModuleNotFoundError as error:
        raise ImportError(
            f"No database driver installed for {url_object.get_backend_name()!r}. "
            "For Postgres, install the extra: uv sync --extra postgres"
        ) from error


class SqlRunRepository:

    def __init__(self, url: str | sa.URL) -> None:
        self._engine = _engine_for(url)
        _METADATA.create_all(self._engine, tables=[_RUNS])

    def record(self, run: RunRecord) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                sa.delete(_RUNS).where(_RUNS.c.run_id == run.manifest.run_id)
            )
            connection.execute(
                sa.insert(_RUNS).values(
                    run_id=run.manifest.run_id,
                    job=run.job,
                    status=str(run.manifest.status),
                    ended_at=run.ended_at,
                    payload=run.to_dict(),
                )
            )

    def list_runs(self, job: str) -> tuple[RunRecord, ...]:
        query = (
            sa.select(_RUNS.c.payload)
            .where(_RUNS.c.job == job)
            .order_by(_RUNS.c.ended_at)
        )
        with self._engine.connect() as connection:
            payloads = connection.execute(query).scalars().all()
        return tuple(RunRecord.from_dict(payload) for payload in payloads)

    def last_healthy(self, job: str) -> RunRecord | None:
        query = (
            sa.select(_RUNS.c.payload)
            .where(_RUNS.c.job == job, _RUNS.c.status == str(RunStatus.OK))
            .order_by(_RUNS.c.ended_at.desc())
            .limit(1)
        )
        with self._engine.connect() as connection:
            payload = connection.execute(query).scalar_one_or_none()
        return RunRecord.from_dict(payload) if payload is not None else None


class SqlProfileRepository:

    def __init__(self, url: str | sa.URL) -> None:
        self._engine = _engine_for(url)
        _METADATA.create_all(self._engine, tables=[_PROFILES])

    def save(self, version: ProfileVersion) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                sa.delete(_PROFILES).where(
                    _PROFILES.c.name == version.name,
                    _PROFILES.c.content_hash == version.content_hash,
                )
            )
            connection.execute(
                sa.insert(_PROFILES).values(
                    name=version.name,
                    content_hash=version.content_hash,
                    effective_from=version.effective_from,
                    payload=version.to_dict(),
                )
            )

    def resolve_as_of(self, name: str, on_date: date) -> ProfileVersion | None:
        query = (
            sa.select(_PROFILES.c.payload)
            .where(_PROFILES.c.name == name, _PROFILES.c.effective_from <= on_date)
            .order_by(_PROFILES.c.effective_from.desc(), _PROFILES.c.content_hash.desc())
            .limit(1)
        )
        with self._engine.connect() as connection:
            payload = connection.execute(query).scalar_one_or_none()
        return ProfileVersion.from_dict(payload) if payload is not None else None

    def get(self, name: str, content_hash: str) -> ProfileVersion | None:
        query = sa.select(_PROFILES.c.payload).where(
            _PROFILES.c.name == name, _PROFILES.c.content_hash == content_hash
        )
        with self._engine.connect() as connection:
            payload = connection.execute(query).scalar_one_or_none()
        return ProfileVersion.from_dict(payload) if payload is not None else None

    def versions(self, name: str) -> tuple[ProfileVersion, ...]:
        query = (
            sa.select(_PROFILES.c.payload)
            .where(_PROFILES.c.name == name)
            .order_by(_PROFILES.c.effective_from, _PROFILES.c.content_hash)
        )
        with self._engine.connect() as connection:
            payloads = connection.execute(query).scalars().all()
        return tuple(ProfileVersion.from_dict(payload) for payload in payloads)
