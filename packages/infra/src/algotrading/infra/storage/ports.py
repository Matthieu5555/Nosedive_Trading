"""Repository ports: the typed contracts that storage backends satisfy.

A consumer depends on one of these Protocols, never on a concrete store, so a backend
can be swapped without touching the consumer. The ports expose only the typed methods
already consumed — always on typed row dataclasses, never an Arrow table or a filesystem
``Path``. Structural typing means a store satisfies its port without inheriting from it;
all ports are ``@runtime_checkable`` so a conformance test can assert the relationship
cheaply.

Ports defined here (metadata/serving tier — M10's domain):
    ``RunRepository``   — one durable record per job run; healthy-run lookup.

Ports for the analytics data plane (M1's domain, added here as stubs so the import tree
stays consistent when callers import from this module):
    See ``algotrading.infra.contracts.ports.StorageRepository`` for the main analytics port.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .runs import RunRecord


@runtime_checkable
class RunRepository(Protocol):
    """The run registry: one durable record per job run, with healthy-run lookup.

    Backend-neutral: persisting a run returns nothing the caller can rely on (a
    filesystem registry returns a ``Path``; a SQLite or Postgres one has none). No
    consumer uses the return value. All three implementations (``RunRegistry``,
    ``SqliteRunRepository``, ``PostgresRunRepository``) satisfy this port structurally.
    """

    def record(self, run: RunRecord) -> None:
        """Persist one run record, keyed by ``run_id`` (idempotent: same id overwrites)."""
        ...

    def list_runs(self, job: str) -> tuple[RunRecord, ...]:
        """All records for a job, oldest run first by end time, or ``()`` if none."""
        ...

    def last_healthy(self, job: str) -> RunRecord | None:
        """The most recent healthy (``status == RunStatus.OK``) run for a job, or ``None``."""
        ...
