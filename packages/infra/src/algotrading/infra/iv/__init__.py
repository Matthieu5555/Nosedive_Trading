"""IV solver — invert the pricing engine for implied volatility (step 8).

:func:`solve_iv` is the European entry point (a price in, an :class:`IvResult` out);
:func:`solve_iv_batch` runs many; :func:`solve_implied_vol_scalar` is the
engine-agnostic bracketed primitive that inverts any monotone pricer (so American
inversion is "via the chosen pricer" by passing its ``price_fn``). :func:`iv_point`
projects a converged result into the stamped ``IvPoint`` contract.

    from algotrading.infra.iv import solve_iv, solve_iv_batch, iv_point, IvRequest
"""

from __future__ import annotations

from .solver import (
    SOLVER_VERSION,
    STATUS_ABOVE_MAX,
    STATUS_BELOW_INTRINSIC,
    STATUS_CONVERGED,
    STATUS_NON_CONVERGENCE,
    IvRequest,
    IvResult,
    SolveOutcome,
    european_price_bounds,
    iv_point,
    solve_implied_vol_scalar,
    solve_iv,
    solve_iv_batch,
)

__all__ = [
    "SOLVER_VERSION",
    "STATUS_ABOVE_MAX",
    "STATUS_BELOW_INTRINSIC",
    "STATUS_CONVERGED",
    "STATUS_NON_CONVERGENCE",
    "IvRequest",
    "IvResult",
    "SolveOutcome",
    "european_price_bounds",
    "iv_point",
    "solve_implied_vol_scalar",
    "solve_iv",
    "solve_iv_batch",
]
