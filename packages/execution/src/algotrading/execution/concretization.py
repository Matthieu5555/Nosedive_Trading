from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    InstrumentKey,
    MarketStateSnapshot,
    ProjectedOptionAnalytics,
)
from algotrading.infra.orders import Side, TicketLeg
from algotrading.infra.risk.multileg import CellKey, analytics_cell_key

_STRIKE_REL_TOL = 1e-9
_STRIKE_ABS_TOL = 1e-6

MARK_SOURCE_SNAPSHOT_MID = "snapshot_mid"
MARK_SOURCE_ANALYTICS_PRICE = "analytics_model_price"


class ConcretizationError(Exception):

    def __init__(self, reason: str, *, cell: CellKey) -> None:
        self.reason = reason
        self.cell = cell
        super().__init__(f"cell={cell!r}: {reason}")


@dataclass(frozen=True, slots=True)
class ConcreteFill:

    contract_key: str
    instrument: InstrumentKey
    underlying: str
    side: Side
    quantity: float
    fill_price: float
    mark_source: str
    as_of: date
    tenor_label: str
    delta_band: str


def option_right_for_band(delta_band: str, target_delta: float) -> str:
    if delta_band.endswith("p"):
        return "P"
    if delta_band.endswith("c"):
        return "C"
    return "P" if target_delta < 0.0 else "C"


@dataclass(frozen=True, slots=True)
class ConcreteChain:

    analytics_rows: tuple[ProjectedOptionAnalytics, ...]
    listed_contracts: tuple[InstrumentKey, ...]
    snapshot_by_contract_key: Mapping[str, MarketStateSnapshot]

    @classmethod
    def build(
        cls,
        *,
        analytics_rows: Iterable[ProjectedOptionAnalytics],
        listed_contracts: Iterable[InstrumentKey],
        snapshots: Iterable[MarketStateSnapshot],
    ) -> ConcreteChain:
        return cls(
            analytics_rows=tuple(analytics_rows),
            listed_contracts=tuple(listed_contracts),
            snapshot_by_contract_key={
                snapshot.instrument_key: snapshot for snapshot in snapshots
            },
        )


def _analytics_cell_index(
    rows: Sequence[ProjectedOptionAnalytics],
) -> tuple[dict[CellKey, ProjectedOptionAnalytics], set[CellKey]]:
    by_cell: dict[CellKey, ProjectedOptionAnalytics] = {}
    ambiguous: set[CellKey] = set()
    for row in rows:
        if row.surface_side != SURFACE_SIDE_COMBINED:
            continue
        key = analytics_cell_key(row.underlying, row.tenor_label, row.delta_band)
        if key in by_cell and by_cell[key].provider != row.provider:
            ambiguous.add(key)
        by_cell[key] = row
    return by_cell, ambiguous


def _strikes_match(listed: float, target: float) -> bool:
    return math.isclose(listed, target, rel_tol=_STRIKE_REL_TOL, abs_tol=_STRIKE_ABS_TOL)


def _resolve_listed_contract(
    *,
    underlying: str,
    strike: float,
    right: str,
    as_of: date,
    listed_contracts: Sequence[InstrumentKey],
    cell: CellKey,
) -> InstrumentKey:
    candidates = [
        contract
        for contract in listed_contracts
        if contract.is_option()
        and contract.underlying_symbol == underlying
        and contract.option_right == right
        and contract.strike is not None
        and _strikes_match(contract.strike, strike)
    ]
    live = [c for c in candidates if c.expiry is not None and c.expiry >= as_of]
    if not live:
        raise ConcretizationError("no_listed_contract", cell=cell)
    soonest_expiry = min(c.expiry for c in live if c.expiry is not None)
    on_soonest = [c for c in live if c.expiry == soonest_expiry]
    if len(on_soonest) > 1:
        raise ConcretizationError("strike_ambiguous", cell=cell)
    return on_soonest[0]


def _paper_mark(
    *,
    contract_key: str,
    analytics_price: float,
    snapshot_by_contract_key: Mapping[str, MarketStateSnapshot],
    cell: CellKey,
) -> tuple[float, str]:
    snapshot = snapshot_by_contract_key.get(contract_key)
    if snapshot is not None:
        mid = (snapshot.bid + snapshot.ask) / 2.0
        if math.isfinite(mid) and mid > 0.0:
            return mid, MARK_SOURCE_SNAPSHOT_MID
    if math.isfinite(analytics_price) and analytics_price > 0.0:
        return analytics_price, MARK_SOURCE_ANALYTICS_PRICE
    raise ConcretizationError("no_mark", cell=cell)


def concretize(
    ticket_leg: TicketLeg,
    *,
    as_of: date,
    chain: ConcreteChain,
) -> ConcreteFill:
    cell = analytics_cell_key(
        ticket_leg.underlying, ticket_leg.tenor_label, ticket_leg.delta_band
    )
    if ticket_leg.instrument_kind != "option":
        raise ConcretizationError("not_an_option_leg", cell=cell)

    by_cell, ambiguous = _analytics_cell_index(chain.analytics_rows)
    if cell in ambiguous:
        raise ConcretizationError("provider_ambiguous", cell=cell)
    row = by_cell.get(cell)
    if row is None:
        raise ConcretizationError("no_analytics_row", cell=cell)

    right = option_right_for_band(row.delta_band, row.target_delta)
    instrument = _resolve_listed_contract(
        underlying=ticket_leg.underlying,
        strike=row.strike,
        right=right,
        as_of=as_of,
        listed_contracts=chain.listed_contracts,
        cell=cell,
    )
    contract_key = instrument.canonical()
    fill_price, mark_source = _paper_mark(
        contract_key=contract_key,
        analytics_price=row.price,
        snapshot_by_contract_key=chain.snapshot_by_contract_key,
        cell=cell,
    )

    return ConcreteFill(
        contract_key=contract_key,
        instrument=instrument,
        underlying=ticket_leg.underlying,
        side=ticket_leg.side,
        quantity=ticket_leg.quantity,
        fill_price=fill_price,
        mark_source=mark_source,
        as_of=as_of,
        tenor_label=ticket_leg.tenor_label or "",
        delta_band=ticket_leg.delta_band or "",
    )
