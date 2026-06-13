# REP1 — scipy / numpy micro-swaps

> **CLOSED — WON'T-FIX (2026-06-11).** Both candidate swaps were investigated and **rejected**
> because they break content-hash stability (the spec's own constraint): `np.interp` differs from
> `_interpolate_sorted` by 1 ULP on ~6/201 interior points (different FP eval order); and
> `scipy.stats.theilslopes` uses a different intercept convention than `theil_sen_line` (shifts the
> intercept by median(y) → changes which strikes are flagged outliers). Verdict machine-checked by
> parity tests. The two heavy numerics (brentq, least_squares) were already on scipy; the remainder
> is deliberately bespoke. **No action.** (Was: READY — see audit action plan Progress.)

- **Owns:** `packages/infra/src/algotrading/infra/surfaces/fit.py` (`_interpolate_sorted`);
  optionally `packages/infra/src/algotrading/infra/utils/robust.py` (`theil_sen_line`) and
  its consumer `forwards/estimate.py:402-433`.
- **Depends on:** nothing.
- **Blocks:** nothing.
- **State going in:** the two heavy numerical jobs (root-find, nonlinear LSQ) are already on
  scipy. Most remaining hand-rolled math is deliberately bespoke (closed-form SVI derivatives,
  the `math.erf` normal CDF kept distinct from the scipy.stats test oracle) and must stay.

## Objective

Delete the two small hand-rolled numerics that a stdlib/scipy call does identically, without
disturbing the deterministic core or the engine-vs-oracle test separation.

## What to do (ordered)

1. **`surfaces/fit.py:87-98` `_interpolate_sorted` → `numpy.interp`.** numpy's flat-end
   default matches the current clamp. **Re-verify** the `k<=ks[0]` / `k>=ks[-1]`
   extrapolation edges match bit-for-bit before deleting the loop. ~10 LOC. Low risk.
2. **(Optional, lower priority) `utils/robust.py:99-120` `theil_sen_line` →
   `scipy.stats.theilslopes`.** **Parity break:** scipy's intercept convention is
   `median(y) − slope·median(x)`, the repo uses `median(yᵢ − slope·xᵢ)` — *different
   estimators*. Golden / cross-process-hash fixtures will shift, and the outlier-rejection
   path in `forwards/estimate.py:402-433` must be re-checked to confirm it still flags the
   same strikes. Only do this with a deliberate fixture re-bless. Defer unless consolidating
   regression backends.

## Done when

Root gate green; for step 1, a test asserts numpy.interp output equals the old routine on
in-range and edge points. Step 2 only merges with re-blessed fixtures and a green
`check-lookahead-bias` / property pass on the parity fit.
