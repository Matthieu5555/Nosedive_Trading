"""Replay-vs-live comparison: do a reconstruction's outputs match what's on disk.

For a date that was already run live, this compares a fresh reconstruction's derived
outputs to the previously-persisted (live) rows, per table, and reports agreement or
the specific divergence. Under the same code version the two must agree — that is the
determinism guarantee the whole actor design exists to make true (ADR 0007, decision
2). This helper does not assume agreement; it measures it, naming the first table and
the exact primary keys that differ, so a future drift surfaces as a pointed failure
rather than a vague mismatch.

It compares *values*, not Parquet bytes: it reads the live rows back as contracts and
matches them against the reconstruction's :class:`ActorOutputs` by primary key, then
by full field equality. Frozen-dataclass equality makes the per-row check exact.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from actor import ActorOutputs
from contracts import (
    ForwardCurvePoint,
    IvPoint,
    MarketStateSnapshot,
    PricingResult,
    RiskAggregate,
    ScenarioResult,
    SurfaceGrid,
    SurfaceParameters,
    table_for_contract,
)
from storage import ParquetStore
from storage.adapter import primary_key_of
from storage.partitioning import trade_date_of

from .report import ReplayComparison, TableAgreement

# The derived tables an actor run lands in, paired with the ActorOutputs attribute that
# holds them. Compared in this fixed order so a comparison report reads the same way
# every time and the "first divergent table" is deterministic.
_TABLES: tuple[tuple[type, str], ...] = (
    (MarketStateSnapshot, "snapshots"),
    (ForwardCurvePoint, "forwards"),
    (IvPoint, "iv_points"),
    (SurfaceParameters, "surface_parameters"),
    (SurfaceGrid, "surface_grid"),
    (PricingResult, "pricings"),
    (RiskAggregate, "risk_aggregates"),
    (ScenarioResult, "scenarios"),
)


def _keyed(table: str, records: Sequence[object]) -> dict[tuple[object, ...], object]:
    """Index records by their primary-key tuple for the table."""
    return {primary_key_of(table, record): record for record in records}


def _compare_table(
    table: str,
    replay_records: Sequence[object],
    live_records: Sequence[object],
) -> TableAgreement:
    """Agreement for one table: equal key sets and equal records under each key.

    A key present on only one side, or a record that differs field-for-field under a
    shared key, is a divergence and is named. Frozen-dataclass ``==`` is the exact
    per-row check; the divergent keys are returned sorted by their string form so the
    report order is stable.
    """
    replay_by_key = _keyed(table, replay_records)
    live_by_key = _keyed(table, live_records)
    divergent: set[tuple[object, ...]] = set()
    for key in set(replay_by_key) | set(live_by_key):
        replay_record = replay_by_key.get(key)
        live_record = live_by_key.get(key)
        if replay_record != live_record:
            divergent.add(key)
    return TableAgreement(
        table=table,
        agrees=not divergent,
        replay_count=len(replay_records),
        live_count=len(live_records),
        divergent_keys=tuple(sorted(divergent, key=repr)),
    )


def compare_replay_to_live(
    store: ParquetStore,
    trade_date: date,
    reconstruction: ActorOutputs,
    *,
    version: str | None = None,
) -> ReplayComparison:
    """Compare a day's reconstruction to the live rows persisted for that day.

    Reads each derived table's rows back from ``store`` for ``trade_date`` (the live
    outputs) and compares them to ``reconstruction``'s tuples, per table, by primary
    key then by full value. ``version`` reads a specific restatement's rows back
    instead of the live (unversioned) layer — pass it to compare two restatements;
    leave it ``None`` to compare against the live analytic. Returns a
    :class:`ReplayComparison` whose ``agrees`` is True only when every table matches.

    The read is scoped to ``trade_date`` only, deliberately *not* to a single
    underlying: the derived tables partition under different underlying values for one
    day — option/IV/surface tables under the real symbol, the portfolio-level
    :class:`contracts.RiskAggregate` under a synthetic ``_all`` partition — so a
    per-underlying scope would silently drop the risk rows from the comparison. The
    per-table primary key already isolates each row.

    The live rows must already be on disk; this does not run the live path. The
    intended use is: live ran and persisted earlier, then a reconstruction of the same
    day is compared here to prove they did not drift.
    """
    table_agreements: list[TableAgreement] = []
    for contract_type, attribute in _TABLES:
        table = table_for_contract(contract_type)
        replay_records = getattr(reconstruction, attribute)
        all_records = store.read(table, version=version)
        live_records = [
            record for record in all_records if trade_date_of(record) == trade_date
        ]
        table_agreements.append(_compare_table(table, replay_records, live_records))
    return ReplayComparison(trade_date=trade_date, tables=tuple(table_agreements))
