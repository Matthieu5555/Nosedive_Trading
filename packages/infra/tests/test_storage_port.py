"""Round-trip the frozen `StorageRepository` port against a trivial in-memory fake.

M0 freezes the port; M1 implements it for real (Parquet/tiered/EAV). This proves the
contract is satisfiable and that its load-bearing semantics — version=None vs an
explicit restatement never mixing, append-only refusing a versioned write — are
expressible through the seam, so M1/M4 build against a proven contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

import pytest
from algotrading.core import source_ref, stamp
from algotrading.infra.contracts import (
    ForwardCurvePoint,
    RawMarketEvent,
    StorageRepository,
    spec_for_table,
)
from algotrading.infra.contracts.bundles import ForwardDiagnostics

_TS = datetime(2026, 6, 5, 14, 30, tzinfo=UTC)
_TD = date(2026, 6, 5)


class VersionedWriteNotAllowed(Exception):
    """The fake's analogue of M1's append-only-versioning guard."""


class InMemoryRepository:
    """A minimal store that honours the port's versioned-restatement semantics.

    Keyed by (table, version) so live rows (version=None) and a restatement
    (version="V") never share a slot — the separation the port promises.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str | None], list[object]] = {}

    def write(
        self, table: str, records: Sequence[object], *, version: str | None = None
    ) -> None:
        if version is not None and spec_for_table(table).append_only:
            raise VersionedWriteNotAllowed(table)
        self._store.setdefault((table, version), []).extend(records)

    def read(
        self,
        table: str,
        *,
        trade_date: date | None = None,
        underlying: str | None = None,
        version: str | None = None,
    ) -> list[object]:
        rows = list(self._store.get((table, version), []))
        if underlying is not None:
            rows = [r for r in rows if getattr(r, "underlying", None) == underlying]
        return rows

    def list_partitions(self, table: str) -> list[tuple[date, str]]:
        seen: set[tuple[date, str]] = set()
        for (tbl, _version), rows in self._store.items():
            if tbl != table:
                continue
            for r in rows:
                seen.add((getattr(r, "trade_date", _TD), getattr(r, "underlying", "")))
        return sorted(seen)

    def list_versions(self, table: str, trade_date: date, underlying: str) -> list[str]:
        return sorted(
            v for (tbl, v) in self._store if tbl == table and v is not None
        )

    def delete_partition(
        self,
        table: str,
        trade_date: date,
        underlying: str,
        version: str | None = None,
    ) -> None:
        self._store.pop((table, version), None)

    def source_records_for(self, record: object) -> dict[str, list[object]]:
        prov = getattr(record, "provenance", None)
        if prov is None:
            return {}
        wanted = {(ref.table, ref.primary_key) for ref in prov.source_records}
        out: dict[str, list[object]] = {}
        for (tbl, _v), rows in self._store.items():
            for r in rows:
                key = (tbl, (getattr(r, "session_id", ""), getattr(r, "event_id", "")))
                if key in wanted:
                    out.setdefault(tbl, []).append(r)
        return out

    def raw_events_for(self, derived_record: object) -> list[object]:
        return self.source_records_for(derived_record).get("raw_market_events", [])


def _raw_event() -> RawMarketEvent:
    return RawMarketEvent(
        session_id="sess-1",
        event_id="evt-a",
        instrument_key="SPX|IND|CBOE|USD|1|con-1||",
        exchange_ts=_TS,
        receipt_ts=_TS,
        canonical_ts=_TS,
        field_name="last",
        value=5000.0,
        trade_date=_TD,
        underlying="SPX",
    )


def _forward(version_label: str) -> ForwardCurvePoint:
    return ForwardCurvePoint(
        snapshot_ts=_TS,
        underlying="SPX",
        maturity_years=0.5,
        expiry_date=date(2026, 12, 18),
        day_count="ACT/365F",
        forward=5050.0 if version_label == "live" else 5060.0,
        diagnostics=ForwardDiagnostics(
            method="parity", candidate_count=8, residual_mad=0.1, quality_label="good"
        ),
        source_snapshot_ts=_TS,
        provenance=stamp(
            calc_ts=_TS,
            code_version="algotrading-infra-0.1.0",
            config_hash="cfg",
            source_records=(source_ref("raw_market_events", "sess-1", "evt-a"),),
            source_timestamps=(_TS,),
        ),
    )


def test_fake_satisfies_the_port_structurally() -> None:
    # @runtime_checkable: a store satisfies the port by shape, not by inheritance.
    assert isinstance(InMemoryRepository(), StorageRepository)


def test_raw_event_round_trips_through_the_port() -> None:
    repo: StorageRepository = InMemoryRepository()
    event = _raw_event()
    repo.write("raw_market_events", [event])
    assert repo.read("raw_market_events") == [event]


def test_versioned_restatement_coexists_with_live() -> None:
    repo: StorageRepository = InMemoryRepository()
    live = _forward("live")
    restated = _forward("restated")
    repo.write("forward_curve", [live])  # version=None == live
    repo.write("forward_curve", [restated], version="reproc-2")

    # version=None reads only the live row; the restatement is read by its version.
    assert repo.read("forward_curve") == [live]
    assert repo.read("forward_curve", version="reproc-2") == [restated]
    assert repo.list_versions("forward_curve", _TD, "SPX") == ["reproc-2"]


def test_append_only_table_refuses_a_versioned_write() -> None:
    repo: StorageRepository = InMemoryRepository()
    with pytest.raises(VersionedWriteNotAllowed):
        repo.write("raw_market_events", [_raw_event()], version="v1")


def test_lineage_resolves_raw_events_for_a_derived_record() -> None:
    repo = InMemoryRepository()
    event = _raw_event()
    repo.write("raw_market_events", [event])
    derived = _forward("live")
    assert repo.raw_events_for(derived) == [event]
