"""algotrading.infra.storage — storage layer for the volatility platform.

The storage layer has two tiers with distinct backends and distinct guarantees:

**Analytics data plane (M1 — Parquet/DuckDB, permanent):**
  Raw events, snapshots, IV points, forwards, surfaces, risk lines/aggregates.
  Immutable append-only raw layer (byte-identical replay anchor) +
  versioned derived layer (restatement beside live, never over it).
  Backed by Parquet files; queried by DuckDB. Never migrated to a row-store —
  doing so breaks byte-identical replay (ADR 0015).

**Metadata/serving tier (M10 — SQLite local / Postgres deployed):**
  Run registry, positions, triage, universe. Small, relational, point-looked-up.
  Backends are configuration: ``SqliteRunRepository`` for local/single-host,
  ``PostgresRunRepository`` for multi-host concurrent access.

Ports (Protocols) are defined in ``storage.ports``; concrete backends are never
imported directly by callers — use ``factory.make_run_repository()`` instead.

Public API of this module (metadata tier, M10):
  ``RunRecord``, ``RunStatus``, ``RunRegistry``    — the run record types
  ``SqliteRunRepository``                          — local backend
  ``PostgresRunRepository``                        — deployed backend
  ``RunRepository``                                — the port (Protocol)
  ``make_run_repository``                          — backend factory

Public API of the analytics data plane (M1):
  ``ParquetStore``                                 — the StorageRepository implementation
  ``primary_key_of``, ``arrow_schema``, ``to_row``, ``from_row`` — codec helpers
  ``StorageError`` and subclasses                  — the write/read failure taxonomy
"""

from .adapter import ParquetStore, primary_key_of
from .errors import (
    AppendOnlyViolation,
    DuplicateKeyInBatch,
    SchemaCompatibilityError,
    StorageError,
    VersionedWriteNotAllowed,
)
from .factory import make_run_repository
from .ports import RunRepository
from .runs import RunRecord, RunRegistry, RunStatus
from .schema import arrow_schema
from .serialization import from_row, to_row
from .sqlite_runs import SqliteRunRepository

__all__ = [
    # Analytics data plane (M1) — the StorageRepository implementation + codec.
    "AppendOnlyViolation",
    "DuplicateKeyInBatch",
    "ParquetStore",
    "SchemaCompatibilityError",
    "StorageError",
    "VersionedWriteNotAllowed",
    "arrow_schema",
    "from_row",
    "primary_key_of",
    "to_row",
    # Metadata / serving tier (M10) — the run registry.
    "RunRecord",
    "RunRegistry",
    "RunRepository",
    "RunStatus",
    "SqliteRunRepository",
    "make_run_repository",
]
