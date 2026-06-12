"""The vectorized Black-76 price must agree, cell for cell, with the scalar engine.

The scalar :func:`pricing.black76.price_european` is the oracle (itself validated against a
BSM/QuantLib oracle in ``test_pricing``/``test_scenario``); this pins the array reprice
:func:`pricing.price_european_array` to it to a tight float tolerance across a wide grid of
states, including the degenerate (zero-vol / zero-maturity) discounted-intrinsic branch and
both option rights. The whole point of the array path is that it does *not* drift from the
scalar one — so this is the test that lets the surface trust it.
"""

from __future__ import annotations

import numpy as np
import pytest
from algotrading.infra.pricing import from_forward, price, price_european_array

# A deliberately wide net of states: deep ITM/OTM strikes, short and long maturities, low and
# high vol, two discount factors, both rights — and the two degenerate axes (T=0, sigma=0).
_FORWARDS = (50.0, 100.0, 137.5, 250.0)
_STRIKES = (40.0, 90.0, 100.0, 110.0, 300.0)
_MATURITIES = (0.0, 0.01, 0.25, 1.0, 3.0)
_VOLS = (0.0, 0.05, 0.20, 0.85)
_DISCOUNTS = (1.0, 0.97)
_RIGHTS = ("C", "P")


def _scalar_price(
    *, forward: float, strike: float, maturity: float, vol: float, df: float, right: str
) -> float:
    state = from_forward(
        forward=forward,
        strike=strike,
        maturity_years=maturity,
        volatility=vol,
        discount_factor=df,
        option_right=right,
    )
    return price(state).price


def test_array_price_matches_scalar_engine_cell_for_cell() -> None:
    # Build the full cartesian product of states as flat arrays, price it once, and compare
    # every cell to the scalar engine evaluated on the same state.
    states = [
        (f, k, t, v, df, right)
        for f in _FORWARDS
        for k in _STRIKES
        for t in _MATURITIES
        for v in _VOLS
        for df in _DISCOUNTS
        for right in _RIGHTS
    ]
    priced = price_european_array(
        forward=np.array([s[0] for s in states]),
        strike=np.array([s[1] for s in states]),
        maturity_years=np.array([s[2] for s in states]),
        volatility=np.array([s[3] for s in states]),
        discount_factor=np.array([s[4] for s in states]),
        is_call=np.array([s[5] == "C" for s in states]),
    )
    assert priced.shape == (len(states),)
    for (f, k, t, v, df, right), got in zip(states, priced.tolist(), strict=True):
        expected = _scalar_price(forward=f, strike=k, maturity=t, vol=v, df=df, right=right)
        assert got == pytest.approx(expected, rel=1e-12, abs=1e-12), (f, k, t, v, df, right)


def test_array_price_broadcasts_over_a_grid() -> None:
    # The shape the surface uses: (legs, spot, vol). A 2-leg book over a 3×3 grid prices to a
    # (2, 3, 3) array, each cell equal to the scalar engine at that broadcast state.
    forward = np.array([100.0, 120.0])[:, None, None]
    strike = np.array([100.0, 110.0])[:, None, None]
    maturity = np.array([0.25, 0.5])[:, None, None]
    df = np.array([0.99, 0.98])[:, None, None]
    is_call = np.array([True, False])[:, None, None]
    spot_mult = np.array([0.9, 1.0, 1.1])[None, :, None]
    vols = np.array([0.10, 0.20, 0.30])[None, None, :]

    priced = price_european_array(
        forward=forward * spot_mult,
        strike=strike,
        maturity_years=maturity,
        volatility=vols,
        discount_factor=df,
        is_call=is_call,
    )
    assert priced.shape == (2, 3, 3)
    for leg in range(2):
        for si in range(3):
            for vj in range(3):
                expected = _scalar_price(
                    forward=float(forward[leg, 0, 0]) * float(spot_mult[0, si, 0]),
                    strike=float(strike[leg, 0, 0]),
                    maturity=float(maturity[leg, 0, 0]),
                    vol=float(vols[0, 0, vj]),
                    df=float(df[leg, 0, 0]),
                    right="C" if is_call[leg, 0, 0] else "P",
                )
                assert priced[leg, si, vj] == pytest.approx(expected, rel=1e-12, abs=1e-12)
