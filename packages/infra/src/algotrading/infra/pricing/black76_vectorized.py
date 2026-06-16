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
    forward = np.asarray(forward, dtype=np.float64)
    strike = np.asarray(strike, dtype=np.float64)
    maturity = np.asarray(maturity_years, dtype=np.float64)
    sigma = np.asarray(volatility, dtype=np.float64)
    df = np.asarray(discount_factor, dtype=np.float64)
    call = np.asarray(is_call, dtype=bool)

    degenerate = (maturity <= 0.0) | (sigma <= 0.0)

    call_intrinsic = np.maximum(forward - strike, 0.0)
    put_intrinsic = np.maximum(strike - forward, 0.0)
    intrinsic = df * np.where(call, call_intrinsic, put_intrinsic)

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
