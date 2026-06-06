"""SQLite-backed config-profile store (metadata tier): effective-dated, content-addressed.

The profile store is small, referential, and point-looked-up by ``(name, date)`` or
``(name, content_hash)`` — the same clean fit for a single indexable SQLite file as the run
registry (:mod:`sqlite_runs`). It is the run-time, as-of system of record for config
profiles (ADR 0028's "Next" stage): "replay day D" resolves the profile in force on D, and a
run pins an immutable ``content_hash`` that is never silently mutated. It is metadata only
and never on the deterministic reconstruction path.

Records serialize through the same ``ProfileVersion.to_dict`` everywhere, so a SQLite → JSON
restore (or a future Postgres backend behind the same ``ProfileRepository`` port) needs no
schema migration. Select the backend with ``make_profile_repository()`` in ``factory.py``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .profiles import ProfileVersion

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    name           TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    payload        TEXT NOT NULL,
    PRIMARY KEY (name, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_profiles_name_eff ON profiles (name, effective_from);
"""


class SqliteProfileRepository:
    """SQLite store of config profiles, keyed by ``(name, content_hash)``.

    Satisfies the ``ProfileRepository`` port structurally — no inheritance required. Saving
    the same content twice is idempotent (the content hash is the key); a new resolved config
    writes a new immutable version.
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

    def save(self, version: ProfileVersion) -> None:
        """Persist one profile version, keyed by ``(name, content_hash)`` (idempotent)."""
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO profiles (name, content_hash, effective_from, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    version.name,
                    version.content_hash,
                    version.effective_from.isoformat(),
                    json.dumps(version.to_dict(), sort_keys=True),
                ),
            )

    def resolve_as_of(self, name: str, on_date: date) -> ProfileVersion | None:
        """The version of ``name`` in force on ``on_date`` — latest ``effective_from`` <= it.

        Ties on ``effective_from`` (two versions effective the same day) break by the later
        insert via ``content_hash`` ordering, so resolution is deterministic.
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT payload FROM profiles WHERE name = ? AND effective_from <= ? "
                "ORDER BY effective_from DESC, content_hash DESC LIMIT 1",
                (name, on_date.isoformat()),
            ).fetchone()
        return ProfileVersion.from_dict(json.loads(row[0])) if row else None

    def get(self, name: str, content_hash: str) -> ProfileVersion | None:
        """The exact version a run pinned by ``content_hash``, or ``None``."""
        with self._connect() as con:
            row = con.execute(
                "SELECT payload FROM profiles WHERE name = ? AND content_hash = ?",
                (name, content_hash),
            ).fetchone()
        return ProfileVersion.from_dict(json.loads(row[0])) if row else None

    def versions(self, name: str) -> tuple[ProfileVersion, ...]:
        """All versions of ``name``, oldest ``effective_from`` first, or ``()`` if none."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT payload FROM profiles WHERE name = ? "
                "ORDER BY effective_from, content_hash",
                (name,),
            ).fetchall()
        return tuple(ProfileVersion.from_dict(json.loads(row[0])) for row in rows)
