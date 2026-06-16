from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from algotrading.infra.actor import ActorOutputs
from algotrading.infra.contracts import (
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
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.adapter import primary_key_of
from algotrading.infra.storage.partitioning import trade_date_of

from .report import ReplayComparison, TableAgreement

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
    return {primary_key_of(table, record): record for record in records}


def _compare_table(
    table: str,
    replay_records: Sequence[object],
    live_records: Sequence[object],
) -> TableAgreement:
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
