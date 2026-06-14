"""The concretization seam the booking commit consumes — the interface, not the engine.

ADR 0043 rules that *a booked fill is a concrete contract, resolved at booking time*. The pure,
as-of resolver that turns a grid-cell ticket leg ``(underlying, tenor_label, delta_band)`` into a
concrete ``(strike, expiry, right)`` plus a paper mark is owned by
``tasks/execution-fill-concretization.md`` (built in parallel, not yet merged). This module is
**the booking commit's view of that seam**: the result shape it consumes and the
:class:`LegResolver` protocol it calls, so the commit depends on the *interface* rather than the
parallel module's concrete code.

**Wire-on-merge:** when ``execution-fill-concretization`` lands its resolver, point the booking
commit's caller at it — any object whose call signature matches :class:`LegResolver` and whose
return matches :class:`ResolvedLeg` satisfies the seam structurally, so no commit code changes.
:class:`ResolvedLeg`'s fields (a concrete ``contract_key``, a positive paper ``price``, and a
signed ``Decimal`` quantity) are the agreed shape co-designed with :class:`~..fills.Fill`; a
rename on either side breaks the seam round-trip test loudly. If the merged module exposes a
differently-named result, adapt it here in one place — never widen the commit.

No I/O, no clock, no broker, no credential here — concretization is pure and as-of (the chain is
passed in), exactly as ADR 0043 requires. The booking gate's password is the only secret the
booking chain touches, and it lives in :mod:`~.password_gate`, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from algotrading.infra.orders import Side, TicketLeg


class ConcretizationError(Exception):
    """A grid-cell leg could not be resolved to a concrete, priced contract.

    Labelled (carries the offending ``field``/``value`` + human ``reason``) so the booking
    commit surfaces *why* a leg was unresolvable — never a silent default, never a bare
    exception. Mirrors the unresolvable-cell failure the concretization spec names.
    """

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class ResolvedLeg:
    """A ticket leg resolved to a concrete, priced contract — the seam's result shape.

    ``contract_key`` is the concrete :meth:`InstrumentKey.canonical` string (the booked
    position key, ADR 0043), never a grid cell. ``price`` is the per-contract paper mark from
    the as-of chain (ADR 0043's default rule is the chain mid; the resolver owns and stamps the
    rule) and is strictly positive, matching :class:`~..fills.Fill`'s price contract.
    ``signed_qty`` folds the ticket leg's :class:`Side` into the sign (BUY → positive, SELL →
    negative) as an exact :class:`Decimal`, so a fill sums directly into the book.

    This is the object the commit turns into a :class:`~..fills.Fill`. The field names are
    co-designed with ``execution-fill-concretization`` and the fills ledger — one source.
    """

    contract_key: str
    price: float
    signed_qty: Decimal
    broker_contract_id: str | None = None


class LegResolver(Protocol):
    """The pure, as-of resolver the booking commit calls — one grid-cell leg → one resolved leg.

    A conforming resolver (the real one from ``execution-fill-concretization``, or the
    reference resolver in tests) reads only the passed-in ``chain`` as-of ``as_of`` — no wall
    clock, no broker, no credential — and raises :class:`ConcretizationError` on an
    unresolvable cell. Structural typing: any object with this ``__call__`` satisfies the seam.
    """

    def __call__(self, leg: TicketLeg, *, as_of: date, chain: object) -> ResolvedLeg: ...


def signed_quantity_for(leg: TicketLeg) -> Decimal:
    """Fold a ticket leg's side into a signed exact quantity: BUY → +, SELL → −.

    The ticket carries a positive magnitude with the direction on :class:`Side` (3A's
    convention); a fill carries the sign as a :class:`Decimal` so the book sums fills without
    float drift. Shared here so the real resolver and the reference resolver agree on the one
    sign rule. The magnitude is taken via :class:`Decimal` from the ticket's ``str`` form so an
    integral quantity stays exact.
    """
    magnitude = abs(Decimal(str(leg.quantity)))
    return magnitude if leg.side is Side.BUY else -magnitude
