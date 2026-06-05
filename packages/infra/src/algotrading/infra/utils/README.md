# infra.utils

Shared pure numerical primitives for the analytics core. No I/O, no clock, no RNG.

Owner: **M2 — analytics core**.

## What's here

- **`daycount.py`** — the single day-count source. `year_fraction(start, end, convention)`
  turns a date interval into a year fraction under an explicit `DayCountConvention`
  (`ACT/365F`, `ACT/360`, `30/360`); `YearFraction` binds the number to its convention so a
  bare float never travels alone. Sub-day precision for `datetime` inputs. Raises if `end`
  precedes `start`.
- **`robust.py`** — median-based order statistics that resist a few bad quotes:
  - `median_absolute_deviation(values)` — `median(|x_i - median(x)|)` (Eq 24), unscaled.
  - `robust_zscores(values)` — MAD z-score per value; `None` for all when MAD is zero.
  - `robust_zscore_vs_baseline(value, baseline)` — score one value against a baseline's own
    median/MAD; `±inf` off a flat baseline, never silently zero (the form anomaly detection needs).
  - `outlier_flags(residuals, *, scale_floor)` — per-residual reject flags at z > 3.5, with a
    noise floor so a near-zero MAD on a clean fit does not spuriously flag every point.
  - `theil_sen_line(xs, ys)` — robust `(slope, intercept)` via median of pairwise slopes.
  - `weighted_median(values, weights)` — smallest value whose cumulative weight reaches half.

`MAD_SCALE = 1.4826` rescales MAD into a std-dev-consistent estimator under normality.

## Bake-off note

Merged from both repos (M2). The day-count module is adopted from Vincent's `utils/daycount.py`.
The robust module unifies both sides into one home, in `float` to match the float analytics
pipeline and its golden/cross-process-hash determinism: Vincent's `robust_zscores`,
`robust_zscore_vs_baseline`, and `weighted_median` plus our `theil_sen_line` and the floored
`outlier_flags`. There is one MAD-rejection primitive (`outlier_flags`, the floored residual
variant), not two. See ADR 0021.
