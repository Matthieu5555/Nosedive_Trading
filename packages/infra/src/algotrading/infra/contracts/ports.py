"""The ``StorageRepository`` port — the one storage seam every layer depends on.

A consumer depends on this Protocol, never on a concrete store, so a backend can be
swapped (Parquet-over-DuckDB today, a tiered/EAV store, a future query engine)
without touching the consumer. It reconciles a repository-port abstraction with the
platform's versioned-partition semantics, which are load-bearing and not optional:

* Reads are *table-keyed* and return the typed contract dataclasses — the only
  objects allowed across a seam (see this package's other modules). No Arrow table,
  DuckDB relation, or filesystem ``Path`` crosses this line.
* ``version=None`` addresses the *live*, unversioned layout (replace-in-place);
  an explicit ``version=<V>`` addresses exactly that restatement. The two never
  mix on a read — that separation is what stops a reconstruct-beside-live run from
  double-counting overlapping primary keys.
* Raw, append-only tables (raw events, instrument master) reject a versioned write:
  raw observations are immutable and have no restatement.

Structural typing means a store satisfies this port without inheriting from it; the
Protocol is ``@runtime_checkable`` so a conformance test can assert the relationship
cheaply (M1 owns that test for every concrete store).

This is the analytics *data-plane* port — the blueprint's "one columnar partitioned
store for raw and derived datasets" (Part I). The blueprint's *other* store, the
"relational metadata store for configuration, jobs, and reference entities", is a
separate seam: ``algotrading.infra.storage.ports.RunRepository`` (run registry,
metadata/serving tier — M10's domain). The two are orthogonal by design.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageRepository(Protocol):
    """Read/write/list raw and derived contract records, with versioned restatement.

    All methods are keyed on a ``table`` name from the contract registry and operate
    on the typed contract dataclasses. Concrete stores (Parquet, tiered, EAV) satisfy
    this by shape, never by inheritance.
    """

    # -- writing ----------------------------------------------------------
    def write(
        self, table: str, records: Sequence[object], *, version: str | None = None
    ) -> None:
        """Validate and persist a batch for one table — all or nothing.

        Append-only tables reject a primary key already on disk; other tables replace
        each touched partition (recompute semantics). ``version=None`` writes the live
        layout; ``version=<V>`` lands a restatement *beside* the live partition.
        A versioned write to an append-only table is refused.
        """
        ...

    # -- reading ----------------------------------------------------------
    def read(
        self,
        table: str,
        *,
        trade_date: date | None = None,
        underlying: str | None = None,
        version: str | None = None,
        provider: str | None = None,
    ) -> list[Any]:
        """Read records for a table (optionally one partition) back into contracts.

        ``version=None`` reads only the live rows; an explicit ``version`` reads only
        that restatement. Live rows and restatements coexist on disk; this separation
        keeps a default read from returning both. ``provider`` narrows a
        provider-partitioned read to one source (ADR 0017 / 0034 §4); left ``None`` it
        reads across providers.
        """
        ...

    # -- partition management --------------------------------------------
    def list_partitions(self, table: str) -> list[tuple[date, str]]:
        """List the ``(trade_date, underlying)`` partitions present for a table."""
        ...

    def list_versions(
        self, table: str, trade_date: date, underlying: str, provider: str | None = None
    ) -> list[str]:
        """List the ``version=<V>`` restatements present for one partition (sorted)."""
        ...

    def delete_partition(
        self,
        table: str,
        trade_date: date,
        underlying: str,
        version: str | None = None,
        provider: str | None = None,
    ) -> None:
        """Delete one partition (idempotent). ``version=None`` removes the whole
        partition including restatements; a version removes only that sub-partition.
        Deleting a derived partition never touches the raw layer."""
        ...

    # -- lineage ----------------------------------------------------------
    def source_records_for(self, record: object) -> dict[str, list[Any]]:
        """Return the source records that produced ``record``, grouped by table.

        Resolves each typed reference on the record's provenance stamp by its *full*
        primary key, answering "which source records, in any table, produced this?"
        """
        ...

    def raw_events_for(self, derived_record: object) -> list[Any]:
        """Return the raw events that produced a derived record (the raw-event slice
        of :meth:`source_records_for`)."""
        ...
