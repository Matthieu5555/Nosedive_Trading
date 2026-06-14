"""The fill: the atomic execution event the book is built from.

Plan-of-record conformance (TARGET §5.5 / §6, blueprint Part XV/XIX):

* **Accounting from fills, not orders.** "The book is built from *fills*, never from
  intentions" (§5.5); "Accounting from fills. Not orders, not signals." (§6). A :class:`Fill`
  is one execution of one instrument — never an order, never a signal. The position store
  folds fills; it never writes a position from an intention.

* **A fill names a *concrete* contract.** The 2A basket and the 3A ticket name a grid cell
  ``(underlying, tenor_label, delta_band)`` and defer the concrete strike/expiry/conid to 3B.
  But the blueprint keys a real position by a **concrete** ``contract_key`` (underlying,
  strike, expiry, right), and a fill is a real execution — so a :class:`Fill` carries the
  resolved concrete ``contract_key``. Resolving the grid cell to that key is the *booking*
  layer's job (the gated commit that mints fills); this contract only requires that the key
  be present and concrete.

* **Lineage.** Every fill points back to the booking decision that emitted it
  (``booking_id``) and the originating 2A basket (``source_basket_id``), so a position traces
  to the intention that created it.

* **Signed quantity, exact.** ``signed_qty`` is a ``Decimal`` carrying its own direction
  (positive long, negative short) — the same convention as :class:`BasketLeg` and
  :class:`~algotrading.infra.risk.Position`; downstream code sums it directly and never
  re-applies a side. ``Decimal`` so a running position is an exact contract count, never a
  float drift.

* **Paper only, two gates.** ``mode`` is pinned ``"paper"``: this module imports no broker,
  reads no credential, and exposes no transmit verb. The password-gated *booking* commit and
  the live broker *send* gate (3B) are two different, later gates — neither lives here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from algotrading.core.provenance import ProvenanceStamp

_PAPER = "paper"


class FillError(Exception):
    """A labelled rejection of a malformed fill.

    Carries the offending ``field`` and ``value`` alongside a human ``reason`` so the booking
    layer (and any audit reader) can surface *what* was wrong, never an opaque
    ``ValueError``/``KeyError`` and never a silent default.
    """

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution of one concrete contract — the atom the book is accounted from.

    ``signed_qty`` carries the direction (long > 0, short < 0); ``price`` is the per-contract
    fill price. ``provenance`` is the stamp the booking layer attached (validated at the
    ledger door, not here — this contract validates its own scalar fields). ``broker_contract_id``
    is a reconciliation foreign key only, never part of identity.
    """

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
            # Decimal is the contract; an int/float quantity would reintroduce float drift
            # when positions accumulate, so it is rejected rather than silently coerced.
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
            # The live path is a separate, later owner gate (3B); fills here are paper by
            # construction, mirroring the 3A ticket's paper-only pin.
            raise FillError("fills are paper-only", field="mode", value=self.mode)
        if self.fill_ts.tzinfo is None:
            raise FillError("must be timezone-aware", field="fill_ts", value=self.fill_ts)
        if self.broker_contract_id is not None and not self.broker_contract_id.strip():
            raise FillError(
                "must be a non-empty string when present",
                field="broker_contract_id",
                value=self.broker_contract_id,
            )
