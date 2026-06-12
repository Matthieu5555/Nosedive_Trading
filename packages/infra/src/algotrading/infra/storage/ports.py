"""Repository ports: the typed contracts that storage backends satisfy.

A consumer depends on one of these Protocols, never on a concrete store, so a backend
can be swapped without touching the consumer. The ports expose only the typed methods
already consumed — always on typed row dataclasses, never an Arrow table or a filesystem
``Path``. Structural typing means a store satisfies its port without inheriting from it;
all ports are ``@runtime_checkable`` so a conformance test can assert the relationship
cheaply.

Ports defined here (metadata/serving tier — M10's domain):
    ``RunRepository``     — one durable record per job run; healthy-run lookup.
    ``ProfileRepository`` — effective-dated, content-addressed config profiles; as-of lookup.

Ports for the analytics data plane (M1's domain, added here as stubs so the import tree
stays consistent when callers import from this module):
    See ``algotrading.infra.contracts.ports.StorageRepository`` for the main analytics port.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from .profiles import ProfileVersion
from .runs import RunRecord


@runtime_checkable
class RunRepository(Protocol):
    """The run registry: one durable record per job run, with healthy-run lookup.

    Backend-neutral: persisting a run returns nothing the caller can rely on (a
    filesystem registry returns a ``Path``; a SQL one has none). No consumer uses the
    return value. Both implementations (``RunRegistry``, ``SqlRunRepository``) satisfy
    this port structurally.
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


@runtime_checkable
class ProfileRepository(Protocol):
    """Effective-dated, content-addressed config profiles (ADR 0028's as-of stage).

    A profile name maps to an append-only set of immutable versions; a run pins a
    ``content_hash`` and resolves "the config in force on day D" by ``effective_from``.
    Backend-neutral (``SqlProfileRepository``: SQLite or Postgres by engine URL) —
    consumers depend on this Protocol.
    """

    def save(self, version: ProfileVersion) -> None:
        """Persist one profile version, keyed by ``(name, content_hash)`` (idempotent)."""
        ...

    def resolve_as_of(self, name: str, on_date: date) -> ProfileVersion | None:
        """The version of ``name`` in force on ``on_date`` (latest ``effective_from`` <= it)."""
        ...

    def get(self, name: str, content_hash: str) -> ProfileVersion | None:
        """The exact version a run pinned by ``content_hash``, or ``None``."""
        ...

    def versions(self, name: str) -> tuple[ProfileVersion, ...]:
        """All versions of ``name``, oldest ``effective_from`` first, or ``()`` if none."""
        ...
