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
- **Degeneracy:** the optimizer's `bound_hits` (every SVI parameter pinned against a
  calibration bound, e.g. `rho_lower`) and `converged` verdict travel onto
  `SurfaceFitDiagnostics` too (additive-nullable: pre-existing rows read back `None` —
  unknown, not clean). `degeneracy_reasons(diagnostics)` is the one policy home for
  "is this calibration trustworthy": a railed parameter, a non-converged fit, or an
  arb breach each yields a machine-readable reason. The policy is **flag, never
  reject and never silently serve as clean** (T-vol-surface-correctness) — railing is
  expected on ultra-short truncated captures and clears on its own once real term
  structure is captured.

## Cross-maturity interpolation (Eq 22)

`interpolate_total_variance(slices, k, maturity)` reads the surface at any maturity:
linear in total variance between the two bracketing slices, holding the nearest
slice flat outside the fitted range — the standard calendar-consistent rule.

## Tenor × delta-band projection (`projection.py`, WS 1F)

`project_grid` is the cross-maturity regrid the fit functions above do **not** do. Where
`fit_slice` fits one smile per *listed* maturity and `surface_grid_cells` projects a single
slice onto a log-moneyness bucket grid, `project_grid` takes the *whole* set of per-maturity
fits and produces, for one underlying at one snapshot, a deterministic grid over the
**pinned tenor set** (`10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y`) crossed with a **delta band** —
the 30Δ-put → ATM → 30Δ-call window, sampled at a configured step. The band is **not** a `.py`
literal: it is expanded from `(band_low_delta, band_high_delta, band_step)` in typed config
(`qc_threshold.grid`, ADR 0028) by `ProjectionConfig.from_band` / `delta_band_axis`, so the grid
the projection **emits** and the grid the WS-1H QC **validates** read one band definition and
cannot drift. The pinned default is the prof's **±30Δ *pas-2*** grid: `30dp,28dp,…,02dp,atm,atmp,
02dc,…,30dc` — 15 puts + the two ATM pillars + 15 calls = **32 cells** per spanned tenor. The ATM
pillar is **two** cells at the one ATM-forward strike — the call `atm` and the put `atmp` — so
the two legs of an ATM straddle are both in the grid (the option right comes from the label's
side suffix, `…p`/`…c`; see `_option_right_for_band`). A deep-OTM band point that lands outside
the fitted strike span is a labeled `ProjectionGap`, never a guessed strike. Each cell is a stamped `ProjectedOptionAnalytics`
contract carrying the fitted IV, the model price, and the Greeks in **both** representations
side by side — the decimal per-unit Greeks (source of truth) and the dollar Greeks, each
dollar number tagged with an explicit unit string (OQ-1 / P0.2, ADR 0036).

### Per-side surfaces (`surface_side`, ADR 0048 / R2)

Each cell carries a `surface_side ∈ {put, call, combined}`: which fitted surface its IV came
from. `project_grid` takes the combined fits plus optional `put_slices` / `call_slices` (the
wings fit over put-only / call-only IV points upstream in the actor). It **solves the strike
once off the combined surface** and then emits a row per supplied side at that same strike,
each reading its own wing's IV — so `combined` is bit-for-bit the legacy single-surface grid
(the forward-backing / attribution reference) and `put`/`call` are additive. With no wings
supplied the grid is `combined`-only, unchanged. A wing with no fitted curve at a maturity is a
labeled `ProjectionGap` for that `(cell, side)`, never a guess.

Because put and call price the **same** strike, `put_call_iv_spread(cells)` reads the
put−call IV spread per cell (`put_iv − call_iv`) — a funding/dividend/borrow-skew signal and a
data-quality instrument; `qc.check_put_call_iv_spread` quarantines a blowout past a configured
bound. Every combined-only consumer (basket risk, booking, grid-coverage QC, the CDC view)
filters to `surface_side == "combined"`, so per-side rows never perturb them. The front-end side
toggle and persisted per-side SVI params are a follow-up.

```python
from algotrading.infra.surfaces import (
    ProjectionConfig, SnapshotMarketState, project_grid,
)
from algotrading.core.config import MonetizationConfig

result = project_grid(
    slices,                                   # one SliceFit per listed maturity
    SnapshotMarketState(underlying="AAPL", provider="DERIBIT", spot=100.0,
                        discount_factors={...}),
    snapshot_ts=ts, source_snapshot_ts=ts, calc_ts=ts,
    projection=ProjectionConfig(version="proj-1"),     # tenor grid + delta-band axis
    monetization=MonetizationConfig(version="mon-1"),  # gamma-1% / theta-365 flags
    config_hashes={"universe": ..., "pricing": ...},   # the upstream bundle hashes
)
result.cells   # tuple[ProjectedOptionAnalytics, ...] — produced grid cells
result.gaps    # tuple[ProjectionGap, ...]            — labeled holes (no bare NaN)
```

The two regrids, both **no-look-ahead** (every cell uses only this snapshot's fits and
state):

- **Tenor.** The pinned tenors rarely coincide with listed expiries, so the smile is
  regridded in **total-variance** space (`interpolate_total_variance`, calendar-no-arb,
  Eq 21/22) — never in raw vol. A pinned tenor outside the fitted maturity span is a
  **labeled `ProjectionGap`** (`reason_code="tenor_beyond_span"`), never a silent
  extrapolation. `clamp_to_span` is off by default.
- **Delta.** For each (tenor, delta-band point) the option delta is inverted against the
  fitted IV to recover the strike, in the **spot-delta convention** of `pricing/black76.py`
  (built at `carry == 0` so spot and forward delta coincide — the same pin 1B uses), so the
  band lands on the right strikes and the IV used to price a cell is the IV at its own
  solved strike (no mismatch). A target outside the fitted strike span is a labeled gap
  (`reason_code="delta_out_of_band"`), not a guess.

**Discount factors (F-SURF-01).** `SnapshotMarketState` — the per-underlying snapshot
market state plus its discount-curve resolver — lives in its own module,
`market_state.py` (M30: curve logic apart from regrid logic; import it from
`algotrading.infra.surfaces` as before). The curve in `SnapshotMarketState.discount_factors` is
keyed by the *listed-expiry* maturities the forward estimates priced, which rarely coincide
with the pinned-tenor years — so a cell's factor is **resolved, never exact-matched**:
a `discount_factors_by_tenor` label hit wins outright (the join that cannot drift through
float re-derivation); otherwise the maturity-keyed curve is read via flat-forward
interpolation (linear in `-ln DF` between knots, the nearest knot's zero rate held flat
beyond the span, so `DF(0) → 1`). Only an **empty** curve falls back to
`default_discount_factor` — the documented, explicitly injected no-curve degradation. The
old exact-`get` silently priced every projected cell rate-free.

**One dollar-Greek home.** The five `dollar_*` numbers and the two convention forks (gamma
per 1% vs $1, theta ÷365 vs ÷252) come from `pricing.dollar_greeks` — the projection reuses
it rather than forking a second formula. The unit strings come from the same `UNIT_STRINGS`.

**Config and reproducibility.** The tenor grid and the 30Δ bound are the hashed `universe`
bundle; the gamma/theta flags are the hashed `scenarios` bundle (`MonetizationConfig`); the
delta-band axis and interpolation rule are the validated `ProjectionConfig`, hashed into the
provenance `config_hashes` under the `projection` key (`canonical_json`, so `-0.0` collapses
onto `0.0` and the grid is byte-identical across processes without `PYTHONHASHSEED`). A
`ProjectionConfig` whose `tenor_grid` is not exactly the pinned eight is refused at
construction. The grid is **provider-partitioned** (ADR 0017 / 0034 §4): it lands at
`<root>/analytics/projected_option_analytics/provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]/data.parquet`.
The golden artifact is `tests/golden/projected_option_analytics.json`; regenerate
deliberately with `uv run pytest packages/infra/tests -k golden --regen-golden`.

## The rich fit vs the persisted contracts

`SliceFit` keeps more than the contracts persist: the SVI parameters, the fit RMSE,
the bound-hit flags and convergence verdict (both also persisted via
`SurfaceFitDiagnostics`), the butterfly-violation points, and — crucially — the raw
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
