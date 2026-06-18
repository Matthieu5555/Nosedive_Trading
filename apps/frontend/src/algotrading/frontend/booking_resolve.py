"""Adapt the as-of concretization rule (ADR 0043) to the booking-commit seam.

`algotrading.execution.concretization.concretize` is the pure, look-ahead-guarded
resolver `(grid-cell leg, as_of, chain) -> ConcreteFill`. The booking commit
(`algotrading.execution.booking.book`) speaks a different shape: a `LegResolver`
returning a `ResolvedLeg`. This module is the only place the two meet — it reads
the as-of chain off the BFF store, runs `concretize`, and maps the concrete fill
onto the seam's `ResolvedLeg`. A cell that cannot be concretized is re-raised as
the seam's `ConcretizationError`, which `book` turns into a labelled paper block
(never a 500).
"""

from __future__ import annotations

from datetime import date

from algotrading.execution import ConcreteChain, concretize
from algotrading.execution.booking import ConcretizationError, ResolvedLeg, signed_quantity_for
from algotrading.execution.concretization import ConcretizationError as CellConcretizationError
from algotrading.infra.orders import TicketLeg

from .context import AppContext
from .store_reads import read_for_underlying

_ANALYTICS_TABLE = "projected_option_analytics"
_INSTRUMENT_TABLE = "instrument_master"
_SNAPSHOT_TABLE = "market_state_snapshots"


class StoreLegResolver:
    """A `LegResolver` that concretizes each leg off the BFF store, as-of the trade date.

    The chain is read once per `(underlying, as_of)` and memoized — a single ticket's
    legs share one underlying and one trade date, so the parquet reads happen once.
    """

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._chains: dict[tuple[str, date], ConcreteChain] = {}

    def __call__(self, leg: TicketLeg, *, as_of: date, chain: object) -> ResolvedLeg:
        # `chain` is the store handed in by `book(...)`; we read the as-of chain ourselves.
        concrete = self._chain_for(leg.underlying, as_of)
        try:
            fill = concretize(leg, as_of=as_of, chain=concrete)
        except CellConcretizationError as exc:
            # Translate the concretization-module error (carries .cell) into the booking
            # seam's error (carries .field/.value) so `book` blocks the commit cleanly.
            raise ConcretizationError(exc.reason, field="cell", value=exc.cell) from exc
        return ResolvedLeg(
            contract_key=fill.contract_key,
            price=fill.fill_price,
            signed_qty=signed_quantity_for(leg),
            broker_contract_id=fill.instrument.broker_contract_id,
        )

    def _chain_for(self, underlying: str, as_of: date) -> ConcreteChain:
        cache_key = (underlying, as_of)
        cached = self._chains.get(cache_key)
        if cached is not None:
            return cached
        store = self._ctx.store
        analytics_rows = read_for_underlying(
            store, _ANALYTICS_TABLE, underlying, trade_date=as_of
        )
        # instrument_master rows are keyed by instrument, not `.underlying`, so they cannot
        # go through read_for_underlying — the store's partition filter scopes the read.
        masters = store.read(_INSTRUMENT_TABLE, trade_date=as_of, underlying=underlying)
        snapshots = read_for_underlying(
            store, _SNAPSHOT_TABLE, underlying, trade_date=as_of
        )
        chain = ConcreteChain.build(
            analytics_rows=analytics_rows,
            listed_contracts=[master.instrument for master in masters],
            snapshots=snapshots,
        )
        self._chains[cache_key] = chain
        return chain
