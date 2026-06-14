"""Average implied correlation ρ̄ from the basket-variance identity (Eq. 23, inverse).

The forward identity (``risk/basket.py``) reads a correlation assumption and returns the
implied index variance:

    sigma_I^2 = sum_i w_i^2 sigma_i^2 + rho_bar * (sum_{i!=j} w_i w_j sigma_i sigma_j)

This module solves it the other way — given the *observed* index vol and the constituent
vols/weights, back out the single average correlation ρ̄ that the market is pricing (TARGET
§4 ruling R3, the S1 dispersion entry signal and a correlation-regime diagnostic). With the
two sums named ``own`` and ``cross`` the inversion is closed-form, no root-finder:

    rho_bar = (sigma_I^2 - own) / cross,
    own   = sum_i w_i^2 sigma_i^2,
    cross = (sum_i w_i sigma_i)^2 - own = sum_{i!=j} w_i w_j sigma_i sigma_j.

Pure: weights/vols/index-vol in, one scalar out. The as-of reads that feed it live in
``signal_set.py``; ``cross`` here is exactly the quantity ``basket_variance`` multiplies by
``avg_correlation``, so a round-trip through that function recovers the input ρ̄ (the test).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


class ImpliedCorrelationError(ValueError):
    """The ρ̄ solve was degenerate — the cross term vanished, carrying the offending value.

    ``cross = (sum w_i sigma_i)^2 - sum w_i^2 sigma_i^2`` is the denominator of the inverse.
    It is zero (so ρ̄ is undefined, not zero) for a one-name basket, an all-zero-vol basket,
    or a single name carrying the whole weight — there is no off-diagonal pair to attribute a
    correlation to. Raised carrying ``cross`` rather than returning a fabricated 0.0, which
    would read as "the market prices zero correlation" when the input simply cannot answer.
    """

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
    """Average implied correlation ρ̄ implied by an index vol and its constituents (Eq. 23).

    ``weights`` and ``constituent_vols`` are the as-of per-name index weights and ATM implied
    vols (same order, same length); ``index_vol`` is the index's own ATM implied vol. Returns
    the single ρ̄ that makes the basket identity hold. Not clamped to ``[-1, 1]``: a value
    outside the band is a real diagnostic (the index is pricing more/less co-movement than the
    constituents alone can produce), and clamping would hide it — the consumer interprets it.

    Raises :class:`ImpliedCorrelationError` when the cross term is non-positive (degenerate
    basket) and ``ValueError`` when the inputs are mis-shaped or carry a negative vol.
    """
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
    own = float(np.dot(ws, ws))  # sum_i w_i^2 sigma_i^2
    cross = float(ws.sum() ** 2 - own)  # sum_{i!=j} w_i w_j sigma_i sigma_j
    # The cross term is a sum of products of non-negative quantities, so it is >= 0; it is
    # exactly 0 only in the degenerate cases. A scale-relative floor distinguishes a true
    # zero from floating-point noise around it (mirrors basket_variance's psd_floor logic).
    if cross <= 1e-12 * own:
        raise ImpliedCorrelationError(cross)
    return (index_vol * index_vol - own) / cross
