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
from .delta_hedge_band import (
    DeltaHedgeBand,
    DeltaHedgeBandError,
    HedgeInstruction,
    decide_delta_hedge,
)
from .dispersion_data import StoreBackedDispersionData, dispersion_strategy
from .gamma_data import StoreBackedGammaData, gamma_strategy
from .harness import (
    StrategyContext,
    StrategyStep,
    UnstampedBasketError,
    run_strategy,
)
from .s1_dispersion import (
    DispersionConfig,
    DispersionConstructionError,
    DispersionMarketData,
    DispersionStrategy,
)
from .s3_gamma import (
    GammaConfig,
    GammaConstructionError,
    GammaMarketData,
    GammaStrategy,
)
from .signal_data import signal_snapshot_from_store
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
    "signal_snapshot_from_store",
    # strategy protocol + decision types
    "Strategy",
    "EntryAction",
    "EntryDecision",
    "ExitAction",
    "ExitDecision",
    "MarketState",
    "RebalanceDecision",
    # delta-hedge band (shared rule for S1/S3/S4)
    "DeltaHedgeBand",
    "DeltaHedgeBandError",
    "HedgeInstruction",
    "decide_delta_hedge",
    # harness
    "run_strategy",
    "StrategyContext",
    "StrategyStep",
    "UnstampedBasketError",
    # S1 dispersion strategy (the flagship; first ADR-0048 per-side consumer)
    "DispersionStrategy",
    "DispersionConfig",
    "DispersionMarketData",
    "DispersionConstructionError",
    "StoreBackedDispersionData",
    "dispersion_strategy",
    # S3 gamma-trading strategy (delta-neutral long-gamma scalp on one cheap name)
    "GammaStrategy",
    "GammaConfig",
    "GammaMarketData",
    "GammaConstructionError",
    "StoreBackedGammaData",
    "gamma_strategy",
]
