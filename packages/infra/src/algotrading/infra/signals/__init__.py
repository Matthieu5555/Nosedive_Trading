"""The signal layer — daily, as-of, contract-typed strategy-entry signals (TARGET §4 R3 / §3).

Pure signal math plus the as-of orchestration that persists it. The §3 strategy book triggers
on these readings; this package derives them and writes them as
:class:`~algotrading.infra.contracts.StrategySignal` rows. It is blind to alpha (pure infra):
strategies *read* the persisted signals, they are not imported here.

* :func:`implied_correlation` — average implied correlation ρ̄ from the basket identity (S1).
* :func:`term_structure_slope` — the front-vs-back ATM-vol slope (S5).
* :func:`realized_volatility` / :func:`realized_minus_implied` — the RV−IV spread legs (S2/S3).
* :func:`iv_rank` / :func:`iv_percentile` — where a name's IV sits in its banked range (S3).
* :func:`persist_signal_set` — read as-of, compute every answerable signal, persist the set.
"""

from __future__ import annotations

from .correlation import ImpliedCorrelationError, implied_correlation
from .iv_history import IvRankError, iv_percentile, iv_rank
from .realized_volatility import (
    RealizedVolatilityError,
    realized_minus_implied,
    realized_volatility,
)
from .signal_set import (
    SIGNAL_KIND_IMPLIED_CORRELATION,
    SIGNAL_KIND_IV_RANK,
    SIGNAL_KIND_IV_VS_REALIZED,
    SIGNAL_KIND_TERM_STRUCTURE_SLOPE,
    SIGNAL_LAYER_VERSION,
    SignalConfig,
    SignalInputs,
    build_signals,
    persist_signal_set,
    read_signal_inputs,
)
from .term_structure import TermStructureError, term_structure_slope

__all__ = [
    "SIGNAL_KIND_IMPLIED_CORRELATION",
    "SIGNAL_KIND_IV_RANK",
    "SIGNAL_KIND_IV_VS_REALIZED",
    "SIGNAL_KIND_TERM_STRUCTURE_SLOPE",
    "SIGNAL_LAYER_VERSION",
    "ImpliedCorrelationError",
    "IvRankError",
    "RealizedVolatilityError",
    "SignalConfig",
    "SignalInputs",
    "TermStructureError",
    "build_signals",
    "implied_correlation",
    "iv_percentile",
    "iv_rank",
    "persist_signal_set",
    "read_signal_inputs",
    "realized_minus_implied",
    "realized_volatility",
    "term_structure_slope",
]
