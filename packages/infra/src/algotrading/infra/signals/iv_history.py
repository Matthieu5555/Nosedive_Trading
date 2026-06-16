from __future__ import annotations

from collections.abc import Sequence

import numpy as np


class IvRankError(ValueError):

    def __init__(self, reason: str, history: tuple[float, ...]) -> None:
        self.reason = reason
        self.history = history
        super().__init__(f"IV rank undefined: {reason} (history={history})")


def iv_rank(current: float, history: Sequence[float]) -> float:
    window = tuple(float(v) for v in history)
    if not window:
        raise IvRankError("empty history window", window)
    series = np.asarray(window, dtype=np.float64)
    low = float(series.min())
    high = float(series.max())
    span = high - low
    if span <= 0.0:
        raise IvRankError("flat history window (max == min), zero range", window)
    return float(np.clip((current - low) / span, 0.0, 1.0))


def iv_percentile(current: float, history: Sequence[float]) -> float:
    window = tuple(float(v) for v in history)
    if not window:
        raise IvRankError("empty history window", window)
    series = np.asarray(window, dtype=np.float64)
    return float(np.count_nonzero(series < current) / series.size)
