"""IV rank and IV percentile of a current implied vol within its banked history.

Where in its own recent range is a name's implied vol trading? Two standard readings over a
trailing window of daily ATM IVs (TARGET §3, the S3 entry signal; the banked harvested days
are the raw material):

* **IV rank** — where the current IV sits between the window's min and max, in ``[0, 1]``:
  ``(current - min) / (max - min)``. 0 = at the cheapest the window has seen, 1 = the richest.
* **IV percentile** — the fraction of the window trading strictly below the current IV, in
  ``[0, 1]``. Robust to a single outlier that rank is not.

Pure: a current scalar plus a history sequence in, one scalar out. The history is read as-of
(``trade_date <= as_of``) by the caller; nothing here reaches for a date.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


class IvRankError(ValueError):
    """An IV-rank window was empty or flat (max == min), carrying the window.

    A flat window has a zero range, so ``(current - min) / (max - min)`` is undefined (not
    0.5 or 0.0); an empty window has nothing to rank against. Raised rather than fabricating a
    midpoint, which would read as "mid-range" when the window cannot place the value at all.
    """

    def __init__(self, reason: str, history: tuple[float, ...]) -> None:
        self.reason = reason
        self.history = history
        super().__init__(f"IV rank undefined: {reason} (history={history})")


def iv_rank(current: float, history: Sequence[float]) -> float:
    """Min–max IV rank of ``current`` within ``history``, in ``[0, 1]``.

    ``history`` is the trailing window of daily ATM IVs (current day included is fine — it just
    guarantees the value lands inside ``[min, max]``). Returns ``(current - min) / (max - min)``,
    clamped to ``[0, 1]`` so a ``current`` outside the historical range reads as exactly 0 or 1
    rather than spilling past the band. Raises :class:`IvRankError` for an empty or flat window.
    """
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
    """Fraction of ``history`` trading strictly below ``current``, in ``[0, 1]``.

    ``history`` is the trailing window of daily ATM IVs. Returns the count of window values
    strictly less than ``current`` divided by the window length. An empty window raises
    :class:`IvRankError` — there is nothing to take a percentile against.
    """
    window = tuple(float(v) for v in history)
    if not window:
        raise IvRankError("empty history window", window)
    series = np.asarray(window, dtype=np.float64)
    return float(np.count_nonzero(series < current) / series.size)
