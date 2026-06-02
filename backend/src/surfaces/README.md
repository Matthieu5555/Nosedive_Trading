# surfaces — volatility surface engine (step 9)

Turns the solved implied-vol points for one underlying into a fitted volatility
surface: an SVI smile per maturity, no-arbitrage diagnostics, cross-maturity
interpolation, and the persisted `SurfaceParameters` / `SurfaceGrid` contracts. Pure
functions: no I/O, no clock, no randomness; `calc_ts` is injected at emission.

## TL;DR

```python
from surfaces import fit_slice, surface_parameters, surface_grid_cells

fit = fit_slice("AAPL", maturity_years=0.25, points=iv_points,
                expiry_date=expiry, day_count="ACT/365")
if fit.method == "svi":
    params = surface_parameters(fit, snapshot_ts=ts, source_snapshot_ts=ts,
                                calc_ts=ts, config_hash=cfg_hash)   # SurfaceParameters
grid = surface_grid_cells(fit, moneyness_buckets=(-0.2, 0.0, 0.2),
                          snapshot_ts=ts, source_snapshot_ts=ts,
                          calc_ts=ts, config_hash=cfg_hash)          # SurfaceGrid cells
```

`fit_slice` is **total**: an empty slice returns a labeled `insufficient` fit, never
a raise. Every slice carries a `method` (`svi` / `nonparametric` / `insufficient`)
so a consumer always knows how the curve was built — a fallback is never dressed up
as a calibration.

## SVI per slice (Eq 20)

The smile is fit in total-variance space with the raw SVI form
`w(k) = a + b(ρ(k-m) + sqrt((k-m)² + σ²))`, calibrated by
`scipy.optimize.least_squares` under parameter bounds. `b` and `σ` are bounded
strictly positive, so the fitted parameters always satisfy A's `SurfaceParameters`
contract (`svi_b > 0`, `svi_sigma > 0`). A parameter that pins against a bound is
reported in `bound_hits` (e.g. `"rho_lower"`) — a fit at its feasible edge is
visible, not silently trusted. SVI needs five points (five parameters); with fewer,
`fit_slice` falls back to a **labeled** nonparametric curve (linear interpolation of
the observed total variance, flat beyond the wings) so a sparse slice still produces
a usable surface.

## No-arbitrage diagnostics (reported, not enforced)

- **Calendar (Eq 21):** `calendar_violations` checks total variance is non-decreasing
  in maturity at fixed log-moneyness across adjacent slices; a dip is a
  calendar-spread arbitrage.
- **Butterfly:** `butterfly_violations` evaluates Gatheral's `g(k)` from the SVI
  closed-form derivatives; `g(k) < 0` (or non-positive total variance) is a breach.
  A slice's `arb_free` flag is the butterfly verdict; `SurfaceFitDiagnostics.arb_free`
  carries it onto the contract.

## Cross-maturity interpolation (Eq 22)

`interpolate_total_variance(slices, k, maturity)` reads the surface at any maturity:
linear in total variance between the two bracketing slices, holding the nearest
slice flat outside the fitted range — the standard calendar-consistent rule.

## The rich fit vs the persisted contracts

`SliceFit` keeps more than the contracts persist: the SVI parameters, the fit RMSE,
the bound-hit flags, the butterfly-violation points, and — crucially — the raw
`IvPoint` records, never discarded after calibration, so the fit stays auditable
against its inputs. `surface_parameters` (SVI only) and `surface_grid_cells` (any
method with a curve) project the usable part into A's stamped contracts;
`slice_plot_series` returns raw-vs-fitted plot data.

## Tunable constants (top of `svi.py` / `fit.py` / `arbitrage.py`)

- Parameter bounds `_A_BOUNDS`, `_B_BOUNDS`, `_RHO_BOUNDS`, `_M_BOUNDS`,
  `_SIGMA_BOUNDS` and `_BOUND_HIT_TOL` — the SVI feasible box and the bound-hit
  threshold.
- `MIN_POINTS_FOR_SVI = 5` — below this a slice goes nonparametric.
- `_ARB_GRID_PAD`, `_ARB_GRID_POINTS` — the log-moneyness grid the butterfly check
  probes, padded past the observed strikes.
- `_CALENDAR_TOL`, `_BUTTERFLY_TOL` — float slack so an exactly-flat boundary is not
  flagged.

## Verify

```
cd backend && uv run ruff check src/surfaces && uv run mypy src/surfaces \
  && uv run pytest -q tests/test_surfaces.py
```
