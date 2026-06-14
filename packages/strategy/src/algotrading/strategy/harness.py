"""One logic, four contexts (TARGET §6): the thin calling convention over a ``Strategy``.

TARGET §6: "Research, backtest, paper, and live call the same strategy object." This is that
calling convention — a single function, :func:`run_strategy`, that any of the four contexts
invokes the *same way* on the *same* :class:`~algotrading.strategy.Strategy` instance. The
context supplies the state (a :class:`~algotrading.strategy.signals.SignalSnapshot` and a
:class:`~algotrading.strategy.strategy.MarketState`); the function returns the strategy's
decisions and — only when entry fires — the stamped 2A :class:`Basket` it constructs.

No strategy logic lives here. The harness does not decide; it routes injected state into the
strategy's pure methods and collects the results. That is the whole point: because the
strategy is a pure function of injected state and the harness adds no logic of its own,
``run_strategy(strategy, ...)`` returns the identical :class:`StrategyStep` in research,
backtest, paper, and live given identical inputs — which is what proves "research == paper ==
live" (the production-shadow property ``strategy-backtester`` relies on).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from algotrading.infra.contracts import Basket

from .signals import SignalSnapshot
from .strategy import (
    EntryAction,
    EntryDecision,
    ExitDecision,
    MarketState,
    RebalanceDecision,
    Strategy,
)


class StrategyContext(StrEnum):
    """The four contexts that call the same strategy object (TARGET §6).

    A label only — it does **not** change what the strategy computes (that is the invariant
    the harness guarantees). It rides on the :class:`StrategyStep` so a downstream record can
    say *which* context produced a decision, never so the strategy can branch on it.
    """

    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class StrategyStep:
    """The full result of one strategy decision step in one context.

    ``entry`` / ``exit_`` / ``rebalance`` are the strategy's three decisions over the injected
    state. ``basket`` is the constructed, ``strategy_id``-stamped 2A position set — present
    iff the entry action is ``ENTER`` (a HOLD/NOOP step constructs nothing), so a context can
    book it directly. ``context`` records which of the four produced this step. The whole step
    is a pure function of the strategy plus the injected ``signals``/``market``.
    """

    context: StrategyContext
    as_of: date
    strategy_id: str
    entry: EntryDecision
    exit_: ExitDecision
    rebalance: RebalanceDecision
    basket: Basket | None


def run_strategy(
    strategy: Strategy,
    *,
    context: StrategyContext,
    as_of: date,
    signals: SignalSnapshot,
    market: MarketState,
    basket_id: str,
) -> StrategyStep:
    """Run one decision step of ``strategy`` in ``context`` — the §6 four-context convention.

    The single entry point every context calls identically. It routes the injected state into
    the strategy's pure methods — :meth:`~algotrading.strategy.Strategy.decide_entry`,
    :meth:`~algotrading.strategy.Strategy.decide_exit`,
    :meth:`~algotrading.strategy.Strategy.rebalance` — and constructs the stamped basket only
    when entry fires (``EntryAction.ENTER``). It adds no decision logic, so two contexts that
    inject equal ``signals``/``market`` get equal :class:`StrategyStep`s (the production-
    shadow invariant). The constructed basket is asserted to carry the strategy's stamp, so a
    construct that forgot to stamp is caught at the seam rather than flowing unnamed into
    composition/attribution.
    """
    entry = strategy.decide_entry(as_of, signals)
    exit_ = strategy.decide_exit(market)
    rebalance = strategy.rebalance(market)
    basket = strategy.construct(as_of, basket_id=basket_id) if entry.action is EntryAction.ENTER \
        else None
    if basket is not None and basket.strategy_id != strategy.contract.strategy_id:
        raise UnstampedBasketError(
            strategy.contract.strategy_id, basket.strategy_id
        )
    return StrategyStep(
        context=context,
        as_of=as_of,
        strategy_id=strategy.contract.strategy_id,
        entry=entry,
        exit_=exit_,
        rebalance=rebalance,
        basket=basket,
    )


class UnstampedBasketError(ValueError):
    """A strategy's ``construct`` returned a basket not stamped with its own ``strategy_id``.

    The stamp is the whole seam: an unstamped (or mis-stamped) basket would flow unnamed into
    2D composition and per-strategy attribution, breaking the grouping silently. So the harness
    refuses it at the boundary, carrying both the expected and the actual stamp as evidence.
    """

    def __init__(self, expected: str, actual: str | None) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"construct() emitted a basket stamped {actual!r}, expected the strategy's own "
            f"strategy_id {expected!r} — an unstamped set cannot be grouped by strategy"
        )
