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

The analytics data plane types (M1) will be exported here once M1 merges its
implementation into this package.
"""

from .factory import make_run_repository
from .ports import RunRepository
from .runs import RunRecord, RunRegistry, RunStatus
from .sqlite_runs import SqliteRunRepository

__all__ = [
    "RunRecord",
    "RunRegistry",
    "RunRepository",
    "RunStatus",
    "SqliteRunRepository",
    "make_run_repository",
]
