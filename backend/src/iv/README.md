# iv — implied-volatility solver (step 8)

Inverts an option price for the volatility that reproduces it. This is the one
place in the platform where a round-trip against our own code is legitimate: the
pricing engine maps vol → price, this module inverts that map, and the two are
*different code*, so the pricer is the independent oracle for the solver.

## TL;DR

```python
from config import SolverConfig
from iv import solve_iv, iv_point

cfg = SolverConfig(version="...", iv_tolerance=1e-10, max_iterations=100)
result = solve_iv(
    target_price, contract_key="<canonical key>", forward=F, strike=K,
    maturity_years=T, discount_factor=DF, option_right="C", config=cfg,
)
if result.converged:
    point = iv_point(result, snapshot_ts=ts, source_snapshot_ts=ts,
                     calc_ts=ts, config_hash=cfg_hash)  # stamped IvPoint for the store
```

`solve_iv` is **total**: it never raises on data. A solve that cannot produce a
volatility comes back with a status — `below_intrinsic`, `above_max`, or
`non_convergence` — plus the iteration count, the final price residual, and the
search bracket. It is never a bare `NaN`, and such a result is never projected to an
`IvPoint` (the contract requires a finite `iv >= 0`); `iv_point` raises if you try.

## How it works

The price is monotone increasing in vol, so the inverse is a clean scalar root
find. `solve_implied_vol_scalar` brackets `[1e-9, 5.0]` (zero vol to 500%, far above
any real equity vol) and runs `scipy.optimize.brentq`. Bound checks run first: a
call price lies in `[DF·max(F-K,0), DF·F]`, a put in `[DF·max(K-F,0), DF·K]`, and a
target outside that is impossible — labeled, not chased. A price at the intrinsic
floor has no time value, so the vol is exactly `0`. The result carries the
log-moneyness `k = ln(K/F)` (Eq 6) and the total variance `w = σ²·T` (Eq 7).

## Two layers, and American inversion

`solve_implied_vol_scalar` is engine-agnostic: hand it a `price_fn` from vol to
price and the bounds, and it inverts *any* monotone pricer. `solve_iv` is the
European convenience built on Black-76. To invert an **American** price "via the
chosen pricer", pass that pricer's `price_fn` (a lattice or Bjerksund-Stensland
closure) straight into the scalar primitive — see `test_american_inversion_via_the
_lattice_pricer`. `solve_iv_batch` is a thin order-preserving wrapper that solves
each contract independently, so one pathological quote yields its own labeled
failure without sinking the batch.

## Config you inject

`SolverConfig(version, iv_tolerance, max_iterations)` — `iv_tolerance` is brentq's
`xtol`; `max_iterations` is its `maxiter`. A budget too small for the tolerance
returns a `non_convergence` diagnostic (brentq runs with `disp=False`, so it reports
rather than raises).

## Search-range constants (top of `solver.py`)

- `_VOL_MIN = 1e-9`, `_VOL_MAX = 5.0` — the vol bracket. A price needing more than
  500% vol is reported `above_max` rather than resolved to an absurd number.
- `_PRICE_RTOL`, `_PRICE_ATOL` — the price-space slack for the bound checks.

## Verify

```
cd backend && uv run ruff check src/iv && uv run mypy src/iv \
  && uv run pytest -q tests/test_iv.py
```
