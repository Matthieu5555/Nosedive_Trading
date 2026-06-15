"""The backtest position book — held contracts in, landed :class:`PositionRisk` lines out.

The strategy emits decisions and stamped baskets; the *book* is the running state between them
— what is currently held, marked at today's market. It is a thin, pure ledger over the landed
risk engine: it accumulates :class:`HeldContract`s, drops the ones that have expired by an
as-of date (S2's rolling line is exactly "one expires daily"), and prices the survivors into
:class:`~algotrading.infra.risk.greeks.PositionRisk` lines via the landed
:func:`~algotrading.infra.risk.greeks.position_risk`. It computes no Greeks of its own — it
joins held contracts to their as-of valuations and hands them to the priced-line constructor,
so the lines it produces are byte-identical to the ones the live/paper book would price.

Those :class:`PositionRisk` lines are the one currency three landed consumers already speak:
the strategy's :class:`~algotrading.strategy.MarketState` (the exit/kill and rebalance hooks
read them), the day-over-day attribution (``attribute_realized_book`` takes start-of-day lines),
and the stress grid (``scenario_line_pnls`` takes the same lines). So the book is the single
join point and nothing downstream re-prices.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from algotrading.infra.risk.greeks import PositionRisk, position_risk
from algotrading.infra.risk.valuation import ContractValuationInput

from .data import BacktestData, HeldContract

# The portfolio id every backtest line is stamped with — the netting/attribution key the landed
# engine groups on. One book per backtest run, so one id; an internal label, not business config.
_BACKTEST_PORTFOLIO_ID = "backtest"


@dataclass(frozen=True, slots=True)
class PricedBook:
    """The book priced at one as-of day: the held contracts that could be marked, as lines.

    ``lines`` are the landed :class:`PositionRisk` lines for the contracts that resolved a
    valuation on the day (one per held contract, side-signed). ``valuations`` maps each line's
    ``contract_key`` to the :class:`ContractValuationInput` it was priced at — the end-of-day
    state the next day's attribution reads as *its* start, and the same-day stress grid shocks.
    ``unpriced`` names the held contracts that had no mark that day (expired or data-gapped),
    dropped from the lines rather than invented — a labelled absence, never a phantom mark.
    """

    as_of: date
    lines: tuple[PositionRisk, ...]
    valuations: Mapping[str, ContractValuationInput]
    unpriced: tuple[str, ...]


@dataclass
class BacktestBook:
    """The mutable running ledger of held contracts across the replay (the only mutable state).

    A backtester is inherently a fold over time — the book at day *t* is day *t-1*'s book plus
    that day's adds minus the expiries — so this is the one place the otherwise-pure design holds
    mutable state, and it holds *only* the list of held contracts. Pricing, netting, and risk are
    all delegated to the landed engine through :meth:`price`; this object just remembers what is
    open and applies the daily roll-off.
    """

    held: list[HeldContract] = field(default_factory=list)

    def add(self, contracts: list[HeldContract]) -> None:
        """Open new contracts (a strategy entry day's concretized legs) into the book."""
        self.held.extend(contracts)

    def expire(self, as_of: date) -> list[HeldContract]:
        """Roll off and return every held contract that has expired on or before ``as_of``.

        S2's rolling line is "one put expires each day"; this is that roll, generic over any
        held contract. A contract whose ``expiry`` is at or before the day leaves the book (it no
        longer carries risk and must not be marked into the next day's P&L). Mutates the book to
        the survivors and returns the rolled-off contracts so the caller can record the turnover.
        """
        rolled = [c for c in self.held if c.expiry <= as_of]
        self.held = [c for c in self.held if c.expiry > as_of]
        return rolled

    @property
    def open_contract_count(self) -> float:
        """The count of currently-held contracts (the book's view of the rolling-line size)."""
        return float(len(self.held))

    def price(self, data: BacktestData, as_of: date) -> PricedBook:
        """Price every held contract at ``as_of`` into landed :class:`PositionRisk` lines.

        Reads each held contract's as-of :class:`ContractValuationInput` from the data seam and
        prices it with the landed :func:`position_risk` (the same priced-line constructor the
        live book uses), so the resulting lines are not a backtest-special risk number. A contract
        the seam cannot value on the day is recorded in ``unpriced`` and left out of the lines —
        a labelled absence, never a fabricated mark. Pure of the book's own state beyond reading
        ``held``: same book + same data + same date → same :class:`PricedBook`.
        """
        lines: list[PositionRisk] = []
        valuations: dict[str, ContractValuationInput] = {}
        unpriced: list[str] = []
        for contract in self.held:
            valuation = data.valuation(contract, as_of)
            if valuation is None:
                unpriced.append(contract.contract_key)
                continue
            lines.append(
                position_risk(
                    portfolio_id=_BACKTEST_PORTFOLIO_ID,
                    quantity=contract.quantity,
                    valuation=valuation,
                )
            )
            valuations[contract.contract_key] = valuation
        return PricedBook(
            as_of=as_of,
            lines=tuple(lines),
            valuations=valuations,
            unpriced=tuple(unpriced),
        )
