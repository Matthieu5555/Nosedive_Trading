from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from algotrading.infra.orders import Side, TicketLeg


class ConcretizationError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class ResolvedLeg:

    contract_key: str
    price: float
    signed_qty: Decimal
    broker_contract_id: str | None = None


class LegResolver(Protocol):

    def __call__(self, leg: TicketLeg, *, as_of: date, chain: object) -> ResolvedLeg: ...


def signed_quantity_for(leg: TicketLeg) -> Decimal:
    magnitude = abs(Decimal(str(leg.quantity)))
    return magnitude if leg.side is Side.BUY else -magnitude
