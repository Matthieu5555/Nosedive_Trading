from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from algotrading.infra.contracts import BasketLeg
from algotrading.infra.risk.valuation import ContractValuationInput

from ..signals import SignalSnapshot


@dataclass(frozen=True, slots=True)
class HeldContract:

    contract_key: str
    quantity: float
    expiry: date
    leg: BasketLeg


class BacktestData(Protocol):

    def signals(self, as_of: date) -> SignalSnapshot:
        ...

    def concretize_leg(self, leg: BasketLeg, as_of: date) -> HeldContract | None:
        ...

    def valuation(
        self, held: HeldContract, as_of: date
    ) -> ContractValuationInput | None:
        ...


@dataclass(frozen=True, slots=True)
class ContractMarks:

    by_day: dict[date, ContractValuationInput]


@dataclass(frozen=True, slots=True)
class InMemoryBacktestData:

    signals_by_day: dict[date, SignalSnapshot]
    concrete_by_cell: dict[tuple[str, str | None, str | None, str], HeldContract]
    marks_by_contract: dict[str, ContractMarks]

    def signals(self, as_of: date) -> SignalSnapshot:
        return self.signals_by_day.get(as_of, SignalSnapshot(as_of=as_of, readings=()))

    def concretize_leg(self, leg: BasketLeg, as_of: date) -> HeldContract | None:
        key = (leg.underlying, leg.tenor_label, leg.delta_band, leg.surface_side)
        template = self.concrete_by_cell.get(key)
        if template is None:
            return None
        return HeldContract(
            contract_key=template.contract_key,
            quantity=leg.quantity,
            expiry=template.expiry,
            leg=leg,
        )

    def valuation(
        self, held: HeldContract, as_of: date
    ) -> ContractValuationInput | None:
        marks = self.marks_by_contract.get(held.contract_key)
        if marks is None:
            return None
        return marks.by_day.get(as_of)
