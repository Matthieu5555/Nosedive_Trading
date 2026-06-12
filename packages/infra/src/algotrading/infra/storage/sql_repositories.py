"""SQL-backed metadata tier (runs + profiles) on SQLAlchemy Core — one body, two dialects.

The run registry and the config-profile store are small, referential, point-looked-up
tables — metadata only, never on the deterministic reconstruction path. Each port is
satisfied by exactly one repository class here; the SQLite/Postgres choice is the engine
URL (configuration), not a second hand-written backend. ``factory.make_run_repository`` /
``factory.make_profile_repository`` build the URL — production code never constructs
these classes directly.

Two on-disk formats are deliberately pinned so existing SQLite files keep working and
keep ordering correctly (golden tests in ``test_run_repository.py`` / ``test_profiles.py``):

* ``ended_at`` on SQLite is ISO-8601 **'T'-separated** text — ``datetime.isoformat()``,
  exactly what the previous hand-SQL backend wrote (:class:`IsoDateTimeText`). On
  Postgres it is a real ``TIMESTAMPTZ``.
* ``payload`` on SQLite is canonical sorted-key JSON text — ``json.dumps(payload,
  sort_keys=True)``, byte-identical to the previous backends
  (:class:`CanonicalJsonDocument`). On Postgres it is ``JSONB`` (which normalizes
  storage, as the previous text-into-JSONB insert already did).

Postgres needs ``psycopg`` (``uv sync --extra postgres``); SQLite needs nothing extra.
"""

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
    """Timezone-aware datetime: ``TIMESTAMPTZ`` on Postgres, ISO-8601 'T' text on SQLite.

    The SQLite text form is pinned to ``datetime.isoformat()`` — the bytes the previous
    hand-SQL backend wrote — so existing database files read back unchanged and
    ``ORDER BY ended_at`` sorts old and new rows consistently. SQLAlchemy's stock SQLite
    ``DateTime`` writes a space separator, which sorts *before* 'T' and would mis-order
    mixed rows within a day (the M17 verifier caveat).
    """

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
    """JSON payload column: canonical sorted-key text on SQLite, ``JSONB`` on Postgres.

    SQLite bytes are pinned to ``json.dumps(payload, sort_keys=True)`` — byte-identical
    to the previous hand-SQL backends, so old and new rows share one format. Reads return
    the parsed mapping on both dialects.
    """

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

# Schema identical (names, keys, index) to the previous hand-written DDL, so
# ``create_all(checkfirst=True)`` adopts an existing database file without migration.
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
    """The SQLAlchemy engine URL for a SQLite database file."""
    return sa.URL.create("sqlite", database=str(db_path))


def postgres_engine_url(dsn: str) -> str:
    """Normalize a plain Postgres DSN to SQLAlchemy's psycopg-3 dialect URL.

    ``postgresql://`` / ``postgres://`` (the conventional ``POSTGRES_URL`` forms) select
    the installed ``psycopg`` (v3) driver explicitly; a DSN that already names a
    ``+driver`` passes through untouched.
    """
    scheme, separator, rest = dsn.partition("://")
    if separator and scheme in ("postgres", "postgresql"):
        return f"postgresql+psycopg://{rest}"
    return dsn


def _engine_for(url: str | sa.URL) -> sa.Engine:
    """Create the engine, making the SQLite parent directory and naming a missing driver."""
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
    """SQL registry of job runs; one row per ``run_id`` (re-run overwrites, idempotent).

    Satisfies the ``RunRepository`` port structurally — no inheritance required. One
    implementation for every SQL dialect; pass a SQLite or Postgres engine URL (use
    ``factory.make_run_repository`` rather than constructing this directly).
    """

    def __init__(self, url: str | sa.URL) -> None:
        self._engine = _engine_for(url)
        _METADATA.create_all(self._engine, tables=[_RUNS])

    def record(self, run: RunRecord) -> None:
        """Persist one run record, keyed by ``run_id`` (idempotent: same id overwrites)."""
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
        """All records for a job, oldest run first by end time, or ``()`` if none."""
        query = (
            sa.select(_RUNS.c.payload)
            .where(_RUNS.c.job == job)
            .order_by(_RUNS.c.ended_at)
        )
        with self._engine.connect() as connection:
            payloads = connection.execute(query).scalars().all()
        return tuple(RunRecord.from_dict(payload) for payload in payloads)

    def last_healthy(self, job: str) -> RunRecord | None:
        """The most recent healthy (``status == RunStatus.OK``) run of a job, or ``None``."""
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
    """SQL store of config profiles, keyed by ``(name, content_hash)``.

    Satisfies the ``ProfileRepository`` port structurally. Saving the same content twice
    is idempotent (the content hash is the key); a new resolved config writes a new
    immutable version. Same dialect-by-URL design as :class:`SqlRunRepository`.
    """

    def __init__(self, url: str | sa.URL) -> None:
        self._engine = _engine_for(url)
        _METADATA.create_all(self._engine, tables=[_PROFILES])

    def save(self, version: ProfileVersion) -> None:
        """Persist one profile version, keyed by ``(name, content_hash)`` (idempotent)."""
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
        """The version of ``name`` in force on ``on_date`` — latest ``effective_from`` <= it.

        Ties on ``effective_from`` (two versions effective the same day) break by the later
        ``content_hash``, so resolution is deterministic.
        """
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
        """The exact version a run pinned by ``content_hash``, or ``None``."""
        query = sa.select(_PROFILES.c.payload).where(
            _PROFILES.c.name == name, _PROFILES.c.content_hash == content_hash
        )
        with self._engine.connect() as connection:
            payload = connection.execute(query).scalar_one_or_none()
        return ProfileVersion.from_dict(payload) if payload is not None else None

    def versions(self, name: str) -> tuple[ProfileVersion, ...]:
        """All versions of ``name``, oldest ``effective_from`` first, or ``()`` if none."""
        query = (
            sa.select(_PROFILES.c.payload)
            .where(_PROFILES.c.name == name)
            .order_by(_PROFILES.c.effective_from, _PROFILES.c.content_hash)
        )
        with self._engine.connect() as connection:
            payloads = connection.execute(query).scalars().all()
        return tuple(ProfileVersion.from_dict(payload) for payload in payloads)
