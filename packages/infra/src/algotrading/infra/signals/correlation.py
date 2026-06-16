from __future__ import annotations

from collections.abc import Sequence

import numpy as np


class ImpliedCorrelationError(ValueError):

    def __init__(self, cross: float) -> None:
        self.cross = cross
        super().__init__(
            f"implied-correlation cross term is {cross!r} (<= 0): the basket has no off-diagonal "
            "pair to solve for (one name, zero vols, or one name carrying all the weight)"
        )


def implied_correlation(
    weights: Sequence[float],
    constituent_vols: Sequence[float],
    index_vol: float,
) -> float:
    w = np.asarray(weights, dtype=np.float64)
    s = np.asarray(constituent_vols, dtype=np.float64)
    if w.shape != s.shape or w.ndim != 1:
        raise ValueError(
            f"weights and constituent_vols must be 1-D of equal length, got {w.shape} and "
            f"{s.shape}"
        )
    if w.size == 0:
        raise ValueError("implied_correlation needs at least one constituent, got none")
    if index_vol < 0.0 or bool(np.any(s < 0.0)):
        raise ValueError(
            f"vols must be non-negative, got index_vol={index_vol!r} and constituent_vols={s!r}"
        )

    ws = w * s
    own = float(np.dot(ws, ws))
    cross = float(ws.sum() ** 2 - own)
    if cross <= 1e-12 * own:
        raise ImpliedCorrelationError(cross)
    return (index_vol * index_vol - own) / cross
