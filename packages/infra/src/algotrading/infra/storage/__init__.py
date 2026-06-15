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
  One SQLAlchemy Core repository per port (``sql_repositories.py``); the SQLite/Postgres
  choice is the engine URL the factory builds (``POSTGRES_URL`` selects Postgres, which
  requires ``uv sync --extra postgres``).

Ports (Protocols) are defined in ``storage.ports``; concrete backends are never
imported directly by callers — use ``factory.make_run_repository()`` instead.

Public API of this module (metadata tier, M10):
  ``RunRecord``, ``RunStatus``, ``RunRegistry``    — the run record types
  ``SqlRunRepository``                             — the SQL backend (dialect by URL)
  ``RunRepository``                                — the port (Protocol)
  ``make_run_repository``                          — backend factory

Public API of the analytics data plane (M1):
  ``ParquetStore``                                 — the StorageRepository implementation
  ``primary_key_of``, ``arrow_schema``, ``to_row``, ``from_row`` — codec helpers
  ``StorageError`` and subclasses                  — the write/read failure taxonomy
"""

from .adapter import ParquetStore, primary_key_of
from .compaction import (
    compact_ticker,
    compacted_file_path,
    is_compacted_file,
    list_hot_files_for_ticker,
)
from .errors import (
    AppendOnlyViolation,
    DuplicateKeyInBatch,
    SchemaCompatibilityError,
    StorageError,
    VersionedWriteNotAllowed,
)
from .factory import make_profile_repository, make_run_repository
from .json_io import events_from_json, events_to_json
from .ports import ProfileRepository, RunRepository
from .profiles import (
    ProfileVersion,
    build_profile_version,
    platform_config_from_profile,
)
from .runs import RunRecord, RunRegistry, RunStatus
from .schema import arrow_schema
from .serialization import from_row, to_row
from .sql_repositories import SqlProfileRepository, SqlRunRepository

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
    # Cold-compaction helpers (ADR 0034 §3).
    "compact_ticker",
    "compacted_file_path",
    "is_compacted_file",
    "list_hot_files_for_ticker",
    # Raw-event JSON codec (M5 slice, ADR 0021) — for committed offline samples. The
    # collector-level EAV capture event is ``storage.events.CollectorEvent`` (renamed from
    # ``RawMarketEvent`` to end the collision with the frozen analytics contract
    # ``contracts.tables.RawMarketEvent``); import it explicitly from ``storage.events``.
    "events_from_json",
    "events_to_json",
    # Metadata / serving tier (M10) — the run registry.
    "RunRecord",
    "RunRegistry",
    "RunRepository",
    "RunStatus",
    "SqlRunRepository",
    "make_run_repository",
    # Metadata tier — effective-dated config profiles (C7 / ADR 0028 as-of stage).
    "ProfileRepository",
    "ProfileVersion",
    "SqlProfileRepository",
    "build_profile_version",
    "make_profile_repository",
    "platform_config_from_profile",
]
