# surfaces — volatility surface engine (step 9)

Turns the solved implied-vol points for one underlying into a fitted volatility
surface: an SVI smile per maturity, no-arbitrage diagnostics, cross-maturity
interpolation, and the persisted `SurfaceParameters` / `SurfaceGrid` contracts. Pure
functions: no I/O, no clock, no randomness; `calc_ts` is injected at emission.

## TL;DR

```python
from algotrading.infra.surfaces import fit_slice, surface_parameters, surface_grid_cells

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

`project_surface_fit` is the one seam that owns the *rule* about which method emits
which contract — SVI → parameters and a grid, nonparametric → grid only, insufficient
→ nothing — returning a `SurfaceProjection(parameters, grid_cells)`. A caller (the
actor) persists whatever comes back instead of re-encoding the rule; the method
semantics live here, next to where they are documented.

## Reading the surface out (`reporting.py`)

`reporting.py` reduces the persisted `SurfaceParameters` into display-ready rows for a
CLI table, a test, or an API. `summarize_surface_parameters` returns a
`SurfaceSliceSummary` per maturity (sorted nearest-first), and `atm_volatility` computes
the headline number an operator reads first — at-the-money vol, `sqrt(w(0)/T)` — *from*
the calibrated SVI parameters via the same `SviParams.total_variance`, so the summary
can never drift from the curve it describes.

## Tuning — config vs. code constants

- The SVI feasible box and the bound-hit threshold are **economic inputs**, so
  they live in `SurfaceConfig` (`configs/pricing.yaml` under `surface:`, C7 /
  ADR 0028), not in code: `svi_a_bounds`, `svi_b_bounds`, `svi_rho_bounds`,
  `svi_m_bounds`, `svi_sigma_bounds`, and `svi_bound_hit_tol`.
- `MIN_POINTS_FOR_SVI = 5` — below this a slice goes nonparametric.
- `_ARB_GRID_PAD`, `_ARB_GRID_POINTS` — the log-moneyness grid the butterfly check
  probes, padded past the observed strikes.
- `_CALENDAR_TOL`, `_BUTTERFLY_TOL` — float slack so an exactly-flat boundary is not
  flagged.

## Worked example

At the SVI vertex (`k = m`), the curvature term collapses to `σ`, so
`w(m) = a + b·σ`. For the synthetic parameters `a = 0.04`, `b = 0.10`,
`ρ = -0.30`, `m = 0.0`, `σ = 0.20`, that is `w(0) = 0.04 + 0.10·0.20 = 0.06`. Fitting
`fit_svi` to total-variance points generated from those same parameters recovers all
five to `1e-5` (the generator is the independent oracle). Cross-maturity: place a
calendar-monotone pair of slices and read off `interpolate_total_variance`; beyond the
fitted maturity range it holds the nearest slice flat (e.g. `0.06` extrapolated out
past the last maturity), and inside it interpolates linearly in `w`.

## Determinism, failure modes, and the C-layer boundary

Framework-free pure functions: no clock, no RNG, no I/O; `least_squares` is
deterministic on fixed inputs, so a replay reproduces the SVI parameters exactly, and
`calc_ts` is injected only at projection. The failure modes are all *labeled, not
raised*: a sparse slice (fewer than five distinct strikes) returns a `nonparametric`
fit rather than a forced SVI; an empty slice returns `insufficient`; an arbitrage
breach sets `arb_free = False` and lists the offending `k` points rather than
rejecting the fit; a parameter pinned at its feasible edge is named in `bound_hits`.
The actor (Workstream E) feeds solved `IvPoint` records in and persists the emitted
`SurfaceParameters`/`SurfaceGrid`; it never reaches into the calibration.

## Verify

```
uv run ruff check packages/infra/src/algotrading/infra/surfaces \
  && uv run mypy packages/infra/src/algotrading/infra/surfaces \
  && uv run pytest -q packages/infra/tests/test_surfaces.py
```
