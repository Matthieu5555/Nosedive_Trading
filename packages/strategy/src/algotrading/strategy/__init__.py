"""algotrading.strategy — the shared Strategy spine (TARGET §1/§3/§6).

The foundation the whole strategy lane builds on: the typed strategy *contract* (the four §3
columns — premium / signal / intended Greeks / kill), the ``Strategy`` protocol every S1–S5
object implements (entry / exit-kill decisions + a stamped 2A-basket construction + an
optional band-rebalance hook), the entry-signal input type the strategy *reads*, and the
one-logic-four-contexts harness that lets research, backtest, paper, and live call the same
object identically. Imports infra/core only; never imported by them.
"""

from __future__ import annotations

from .contract import (
    GreekSign,
    IntendedGreeks,
    SignalKind,
    StrategyContract,
    StrategyContractError,
)
from .harness import (
    StrategyContext,
    StrategyStep,
    UnstampedBasketError,
    run_strategy,
)
from .signals import (
    SignalReading,
    SignalSnapshot,
    signal_snapshot,
)
from .strategy import (
    EntryAction,
    EntryDecision,
    ExitAction,
    ExitDecision,
    MarketState,
    RebalanceDecision,
    Strategy,
)

__all__ = [
    # contract
    "StrategyContract",
    "StrategyContractError",
    "IntendedGreeks",
    "GreekSign",
    "SignalKind",
    # signals
    "SignalSnapshot",
    "SignalReading",
    "signal_snapshot",
    # strategy protocol + decision types
    "Strategy",
    "EntryAction",
    "EntryDecision",
    "ExitAction",
    "ExitDecision",
    "MarketState",
    "RebalanceDecision",
    # harness
    "run_strategy",
    "StrategyContext",
    "StrategyStep",
    "UnstampedBasketError",
]
