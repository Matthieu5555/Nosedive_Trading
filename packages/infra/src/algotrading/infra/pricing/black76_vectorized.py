"""Vectorized forward-consistent Black-76 European price (price only), over arrays.

A NumPy reprice of the same closed-form Black-76 the scalar
:func:`pricing.black76.price_european` computes cell by cell — the **price** leg only,
broadcast over whole arrays of state. It exists for the hot grid reprices: the 2B stress
surface reprices a basket over a cartesian spot×vol grid, and one scalar ``price`` call per
cell per leg is thousands of :class:`~pricing.state.PricingState` constructions (each with
its forward-consistency validation) per request. Pricing the whole ``(legs, spot, vol)``
cube as one array removes that per-cell Python overhead.

It is held **bit-faithful** to the scalar engine by a golden test, not by eye: the same
forward-form price ``DF·(F·N(d1) − K·N(d2))``, the same erf-based normal CDF
(``scipy.special.ndtr`` matches the scalar ``math.erf`` form to the ULP), and the same
degenerate branch — where ``maturity_years <= 0`` or ``volatility <= 0`` the option is worth
its discounted intrinsic with no convexity. Greeks are intentionally *not* vectorized here:
the surface needs only price, and the scalar engine stays the one home for Greeks.

All inputs broadcast against one another by the usual NumPy rules, so a basket of ``L`` legs
over an ``S×V`` grid is priced as one ``(L, S, V)`` array. ``is_call`` is a boolean array.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.special import ndtr


def price_european_array(
    *,
    forward: NDArray[np.float64],
    strike: NDArray[np.float64],
    maturity_years: NDArray[np.float64],
    volatility: NDArray[np.float64],
    discount_factor: NDArray[np.float64],
    is_call: NDArray[np.bool_],
) -> NDArray[np.float64]:
    """Black-76 European price for each broadcast state cell (price only).

    Mirrors :func:`pricing.black76.price_european` cell-for-cell: the forward-form price in
    the live regime, the discounted intrinsic on the degenerate cells (zero vol or zero
    maturity — no time value, no convexity). The returned array has the broadcast shape of
    the inputs. Inputs must satisfy the same domain the scalar engine assumes (positive
    forward/strike, non-negative maturity/vol, ``discount_factor`` in ``(0, 1]``); this is a
    hot-path reprice and does not re-validate them.
    """
    forward = np.asarray(forward, dtype=np.float64)
    strike = np.asarray(strike, dtype=np.float64)
    maturity = np.asarray(maturity_years, dtype=np.float64)
    sigma = np.asarray(volatility, dtype=np.float64)
    df = np.asarray(discount_factor, dtype=np.float64)
    call = np.asarray(is_call, dtype=bool)

    degenerate = (maturity <= 0.0) | (sigma <= 0.0)

    # Discounted intrinsic on the degenerate cells (no time value, no convexity).
    call_intrinsic = np.maximum(forward - strike, 0.0)
    put_intrinsic = np.maximum(strike - forward, 0.0)
    intrinsic = df * np.where(call, call_intrinsic, put_intrinsic)

    # Live regime: evaluate on a safe denominator so degenerate cells raise no divide
    # warning, then select. sqrt_t / vol_sqrt_t are strictly positive wherever ~degenerate.
    safe_sigma = np.where(degenerate, 1.0, sigma)
    safe_maturity = np.where(degenerate, 1.0, maturity)
    sqrt_t = np.sqrt(safe_maturity)
    vol_sqrt_t = safe_sigma * sqrt_t
    half_var_t = (safe_sigma * safe_sigma * safe_maturity) / 2.0
    d1 = (np.log(forward / strike) + half_var_t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    nd1, nd2 = ndtr(d1), ndtr(d2)
    call_price = df * (forward * nd1 - strike * nd2)
    put_price = df * (strike * (1.0 - nd2) - forward * (1.0 - nd1))
    live = np.where(call, call_price, put_price)

    return np.where(degenerate, intrinsic, live)
