"""The Nautilus actor — the single glue that drives C/D and stamps their outputs.

The actor holds no math. It transports market state into Workstream C's and D's
pure functions and writes their stamped outputs to A's storage, and because the
same actor runs over a live event stream and over the same events replayed off
disk, surfaces and risk recompute identically live and in replay. The compute step
is a pure function (:func:`run_analytics`) kept separate from persistence
(:func:`persist_outputs`) so the headline replay test can compare two runs as
values.

    from actor import run_analytics, run_day, persist_outputs, ActorOutputs
"""

from __future__ import annotations

from .driver import (
    DEFAULT_MONEYNESS_BUCKETS,
    persist_outputs,
    run_analytics,
    run_day,
)
from .outputs import ActorOutputs
from .stamping import StampSource, build_stamp
from .valuation_join import (
    DEFAULT_EXERCISE_STYLE,
    ValuationJoinError,
    default_exercise_style,
    resolve_valuation_inputs,
)

__all__ = [
    "DEFAULT_EXERCISE_STYLE",
    "DEFAULT_MONEYNESS_BUCKETS",
    "ActorOutputs",
    "StampSource",
    "ValuationJoinError",
    "build_stamp",
    "default_exercise_style",
    "persist_outputs",
    "resolve_valuation_inputs",
    "run_analytics",
    "run_day",
]
