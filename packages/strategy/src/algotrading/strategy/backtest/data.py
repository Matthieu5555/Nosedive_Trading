"""The backtester's as-of data seam — the only place the replay loop touches market state.

The §6 invariant is that the strategy object is a *pure* function of injected state, so the
backtester can never call a store from inside a strategy method. The state has to come from
somewhere, and this module is that somewhere: a :class:`BacktestData` protocol with three
as-of reads — the entry signal, the rolling-line size, and the per-contract valuation — and a
hand-checkable in-memory reference implementor (:class:`InMemoryBacktestData`) the tests drive.

**This is the look-ahead boundary.** Every method takes ``as_of`` and must return only state
known at or before it (the cardinal backtester rule, AGENTS.md / ``check-lookahead-bias``). The
protocol is shaped so a method *cannot* peek forward — it is handed the date and nothing about
the future — and the store-backed implementor (the production path, a documented follow-up) keys
every read to ``trade_date=as_of`` exactly as ``StoreBackedDispersionData`` already does, never a
wall clock.

**Concretize on entry, re-mark each day.** A strategy ``construct`` emits a :class:`Basket` of
*grid-coordinate* legs (underlying / tenor / delta-band — not a concrete strike). On the day a
leg is opened the book must pin it to a concrete contract (a fixed strike and expiry it can mark
and roll); on every later day that fixed contract is re-priced at the new market. So the seam
has two valuation reads — :meth:`BacktestData.concretize_leg` (grid cell → a fixed
:class:`HeldContract` on the entry day) and :meth:`BacktestData.valuation` (an already-held fixed
contract → its :class:`~algotrading.infra.risk.valuation.ContractValuationInput` on any later
day). This mirrors the landed split: execution's ``concretize`` resolves the cell, the infra
valuation join re-marks the contract. The store-backed implementor composes those two; it adds no
pricing math, exactly like the S1/S3 store adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from algotrading.infra.contracts import BasketLeg
from algotrading.infra.risk.valuation import ContractValuationInput

from ..signals import SignalSnapshot


@dataclass(frozen=True, slots=True)
class HeldContract:
    """One concrete, fixed contract the backtest book holds — a pinned grid leg.

    A :class:`~algotrading.infra.contracts.BasketLeg` is a grid coordinate; once opened it is
    pinned to *this* — a fixed ``contract_key`` (the identity attribution and netting join on),
    its signed held ``quantity`` (lots, side-signed), and the ``expiry`` it rolls off at. The
    originating ``leg`` is carried so the day-by-day re-mark can re-resolve the same cell if the
    adapter needs it. The contract is fixed for life: its strike/right never change, only the
    market state it is re-priced at does (that is what makes a day-over-day P&L meaningful).
    """

    contract_key: str
    quantity: float
    expiry: date
    leg: BasketLeg


class BacktestData(Protocol):
    """The as-of market-state seam the replay loop reads — never a store call inside a strategy.

    Three reads, all keyed by ``as_of`` (the look-ahead anchor): the day's entry
    :class:`SignalSnapshot` and the two valuation reads that pin and re-mark contracts. The
    rolling-line size S2's capacity gate needs is **not** a seam read — the backtest book *is* the
    booked line, so the engine reads the count off the book itself, never a separate source that
    could disagree. An implementor returns a labelled absence (``None`` valuation) rather than a
    fabricated mark when a contract cannot be priced on a day — the engine drops an unpriceable
    line from that day's mark rather than inventing P&L.
    """

    def signals(self, as_of: date) -> SignalSnapshot:
        """The entry :class:`SignalSnapshot` as of ``as_of`` (the §6 injected entry input)."""
        ...

    def concretize_leg(self, leg: BasketLeg, as_of: date) -> HeldContract | None:
        """Pin a grid-coordinate leg to a fixed :class:`HeldContract` on its entry day ``as_of``.

        ``None`` when the cell cannot be resolved on the day (no grid row / no listed contract) —
        the engine then does not open the leg rather than booking a phantom contract.
        """
        ...

    def valuation(
        self, held: HeldContract, as_of: date
    ) -> ContractValuationInput | None:
        """The held contract's market state as of ``as_of``, or ``None`` if it cannot be priced.

        Re-prices the *fixed* contract at the new day's market (new spot/vol/maturity), so the
        day-over-day move is a real market move on one instrument. ``None`` is a labelled
        absence (expired, or no market data that day) the engine drops from the mark.
        """
        ...


@dataclass(frozen=True, slots=True)
class ContractMarks:
    """A hand-set per-day market state for one fixed contract in the in-memory reference adapter.

    The test supplies these directly — independently derived spot/vol/maturity per day — so the
    expected P&L and Greeks can be hand-computed against the landed pricer without a store. A
    missing day for a contract is a labelled absence (the adapter returns ``None``), exercising the
    drop-an-unpriceable-line path.
    """

    by_day: dict[date, ContractValuationInput]


@dataclass(frozen=True, slots=True)
class InMemoryBacktestData:
    """A hand-checkable, store-free :class:`BacktestData` for the backtester tests.

    Holds the whole replay's state as plain dicts the test populates with independently derived
    values: ``signals_by_day`` (the entry snapshot per day), ``concrete_by_cell`` (grid coordinate
    → the fixed :class:`HeldContract` to pin, keyed by the leg's
    ``(underlying, tenor_label, delta_band, surface_side)``), and ``marks_by_contract``
    (per-contract per-day :class:`ContractValuationInput`). It performs no
    pricing and no store I/O — it is the fixture seam that lets the engine's orchestration and the
    landed risk/attribution be tested against numbers a human derived by hand.

    Every read is by ``as_of``; an absent day yields the labelled-absence result (an empty
    snapshot, a zero line, a ``None`` concretization/valuation) rather than a fabricated value, so
    the engine's absence-handling paths are exercised by simply omitting a day.
    """

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
        # Re-stamp the template's quantity from the leg actually being opened, so one cell
        # template can serve any size the strategy emits (S2 sells contracts_per_day).
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
