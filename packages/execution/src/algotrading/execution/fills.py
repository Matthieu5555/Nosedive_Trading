from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from algotrading.core.provenance import ProvenanceStamp

_PAPER = "paper"


class FillError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class Fill:

    fill_id: str
    booking_id: str
    source_basket_id: str
    trade_date: date
    underlying: str
    contract_key: str
    signed_qty: Decimal
    price: float
    fill_ts: datetime
    provenance: ProvenanceStamp
    mode: str = _PAPER
    broker_contract_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("fill_id", "booking_id", "source_basket_id", "underlying", "contract_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise FillError("must be a non-empty string", field=name, value=value)
        if not isinstance(self.signed_qty, Decimal):
            raise FillError("must be a Decimal", field="signed_qty", value=self.signed_qty)
        if not self.signed_qty.is_finite():
            raise FillError("must be finite", field="signed_qty", value=self.signed_qty)
        if self.signed_qty == 0:
            raise FillError(
                "must be non-zero (a zero-quantity fill is not an execution)",
                field="signed_qty",
                value=self.signed_qty,
            )
        if not math.isfinite(self.price):
            raise FillError("must be finite", field="price", value=self.price)
        if self.price <= 0:
            raise FillError("must be positive", field="price", value=self.price)
        if self.mode != _PAPER:
            raise FillError("fills are paper-only", field="mode", value=self.mode)
        if self.fill_ts.tzinfo is None:
            raise FillError("must be timezone-aware", field="fill_ts", value=self.fill_ts)
        if self.broker_contract_id is not None and not self.broker_contract_id.strip():
            raise FillError(
                "must be a non-empty string when present",
                field="broker_contract_id",
                value=self.broker_contract_id,
            )
