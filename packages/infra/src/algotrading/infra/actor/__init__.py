"""The framework-free actor — the single glue that drives the analytics/risk core
and stamps their outputs.

The actor holds no math. It transports market state into the pure functions of the
analytics core (``snapshots``/``forwards``/``iv``/``surfaces``/``pricing``) and the
``risk`` engine and writes their stamped outputs to storage, and because the same
actor runs over a live event stream and over the same events replayed off disk,
surfaces and risk recompute identically live and in replay. The compute step is a
pure function (:func:`run_analytics`) kept separate from persistence
(:func:`persist_outputs`) so the headline replay test can compare two runs as values.

    from algotrading.infra.actor import run_analytics, run_day, persist_outputs, ActorOutputs
"""

from __future__ import annotations

from .close_capture import (
    CloseCaptureResult,
    IndexBasket,
    capture_daily_close,
    capture_index_close,
    make_close_capture,
)
from .driver import (
    DEFAULT_MONEYNESS_BUCKETS,
    AnalyticsRun,
    persist_outputs,
    run_analytics,
    run_analytics_with_qc,
    run_day,
)
from .outputs import ActorOutputs
from .qc_inputs import QcInputs
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
    "AnalyticsRun",
    "CloseCaptureResult",
    "IndexBasket",
    "QcInputs",
    "StampSource",
    "ValuationJoinError",
    "build_stamp",
    "capture_daily_close",
    "capture_index_close",
    "default_exercise_style",
    "make_close_capture",
    "persist_outputs",
    "resolve_valuation_inputs",
    "run_analytics",
    "run_analytics_with_qc",
    "run_day",
]
