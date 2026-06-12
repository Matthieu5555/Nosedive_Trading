# T-vol-surface-correctness — make the surface render honestly (projection + SVI + BFF)

> **PRIORITY (owner, 2026-06-12).** Umbrella for the "vol surface looks wrong / SPX API
> returns a degenerate smile" cluster. Owns the **projection + BFF** side (`projection.py`,
> `serializers.py`, surface payload diagnostics). **Disjoint from [T-tenor-selection](T-tenor-selection.md),
> which owns the capture/selection side (`cp_rest_close_capture.py`, `chain_planning.py`).**
> Claim files on the TASKBOARD before editing — the two lanes run in parallel and must not
> double-edit a file.

## The honest picture (verified live 2026-06-12)

The API is **up** — every endpoint returns 200. The numbers are wrong, which reads as "doesn't
work." Two faces of **one** root cause:

1. **Downstream (this task):** projection/BFF mis-prices and mis-renders what little data exists.
   - **F-SURF-01** (`projection.py:251-253`): `discount_factors.get(maturity_years, 1.0)` is keyed
     by **listed-expiry** maturities but queried with **pinned-tenor** years → the float key never
     matches → every projected cell priced **rate-free**, silently. This is what corrupts the SPX
     projected path (`deltas:[0,0]`, flat smile).
   - **SVI degeneracy (new / untracked):** slices rail to the feasible edge — `svi_rho ≈ -0.999`,
     `svi_a ≈ 1e-28`, `arb_free: false`, yet `rmse ≈ 1e-6`. Classic over-fit on **1–5 day**
     maturities. The fit computes `bound_hits`/`converged` but the surface payload only carries
     `rmse`/`n_points`/`arb_free`, so a railed slice is served as if clean.
   - **F-BFF-03** (`serializers.py:188`): missing surface-grid cells filled with `0.0` instead of a
     labeled hole → 3D spikes/flat patches.
   - **F-BFF-04**: fallback path mislabels moneyness-bucket values as deltas / log-moneyness.

2. **Upstream root (T-tenor-selection, separate task):** the capture only keeps the **nearest ~8
   expiries** (all 1–2 weeks out), never the pinned tenor grid — so the surface is fitted on ~2
   weeks and there is **no data at 1m…3y** to price. The broker **does** list 2y/3y (SPX→2031,
   SX5E→2035, measured); we never request them. **This is why** the pinned-tenor DF keys never match
   listed-expiry keys, and **why** SVI only ever sees ultra-short, near-degenerate slices.

**The relationship is the whole point:** F-SURF-01 and the SVI railing are **symptoms** of the
empty term structure. Fixing them makes the surface render *honestly* (rate-correct pricing,
labeled holes, degeneracy flagged) — but the 2y/3y points stay genuinely **absent** until
T-tenor-selection lands **and** a capture re-runs. Do **not** close F-SURF-01 as "the root of the
bad SPX numbers"; it is the projection-side half.

## What to do (ordered — gate green after each)

1. **F-SURF-01 first.** Interpolate the discount factor **at the pinned tenor** from the curve (or
   emit a labeled `ProjectionGap` when the tenor is outside the captured domain) — kill the silent
   rate-free pricing. **Bind on `tenor_label`, not a re-derived `maturity_years` float**, so the key
   matches by construction (the same binding T-tenor-selection establishes at capture). The float
   `.get(..., 1.0)` fallback is forbidden; a miss is a labeled gap, never a silent 1.0.
2. **SVI degeneracy.** Propagate `bound_hits`/`converged` through the slice diagnostics into the
   surface payload and the BFF. Decide and implement the policy when a slice rails (`rho`→bound) or
   `arb_free` is false: **flag** the slice (and prefer rejecting/labeling it over serving it as
   clean). Document that ultra-short (1–5 day) degeneracy is **expected on the current truncated
   capture** and resolves when T-tenor-selection restores longer maturities — i.e. the flag should
   *clear on its own* once real term structure is captured, not be hard-suppressed.
3. **F-BFF-03 / F-BFF-04.** Stop the silent `0.0` hole-fills (label the hole) and fix the
   fallback-axis key mislabeling so the 3D surface and smile render honestly.
4. **No look-ahead.** Projection/serialization read only the snapshot as-of `trade_date`. Run
   `check-lookahead-bias` over the touched paths.

## Test surface

Read `tasks/TESTING.md`. Independent oracles mandatory.

- **F-SURF-01 — independent oracle.** Hand-construct a curve where the pinned-tenor DF ≠ 1.0 and ≠
  any listed-expiry DF; assert the projected cell uses the **interpolated** DF (value computed by
  hand in the comment), not 1.0, and that an out-of-domain tenor yields a **labeled** `ProjectionGap`
  — not a silent 1.0 and not a NaN.
- **SVI degeneracy surfaced.** A slice with `rho` pinned to the bound / `arb_free=false` carries the
  `bound_hits`/`converged` diagnostics through to the BFF payload and is **flagged** (or rejected per
  policy); a clean interior-optimum slice is **not** flagged. Boundary: `rho` exactly at the bound.
- **F-BFF-03.** A surface grid with a genuinely missing cell serializes a **labeled hole**, not
  `0.0`; assert no `0.0` masquerades as a real quote.
- **F-BFF-04.** Fallback-path axis values are labeled with the **correct** axis (moneyness bucket vs
  delta vs log-moneyness); a round-trip asserts the front receives the right key.
- **No look-ahead.** `check-lookahead-bias` clean on projection + serialization.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.

## Done criteria

Projected cells are rate-correct (DF interpolated at the pinned tenor or a labeled gap; no silent
1.0); SVI degeneracy (`bound_hits`, `arb_free=false`, `rho`-rail) is propagated and flagged, not
served as clean; surface-grid holes are labeled not `0.0`-filled; fallback axes are correctly keyed;
no look-ahead; root gate green. The SPX `/api/analytics` smile is no longer a degenerate
`deltas:[0,0]`. **Note in the PR:** full term-structure correctness (real 1m…3y points) depends on
[T-tenor-selection](T-tenor-selection.md) + a re-capture — this task makes the *existing* data
render honestly and the gaps **visible**, not the gaps *filled*.

## Gotchas

- **Symptom, not root.** Do not let F-SURF-01's green tick read as "vol surface fixed." Until
  T-tenor-selection lands and a capture runs, 1m…3y have **no captured data** — the correct output
  is a *labeled gap*, not an interpolated guess masquerading as a quote.
- **Same join key both sides.** Bind on `tenor_label` (the `tenor_years` map at `projection.py:185`
  is the single home). Re-deriving `maturity_years` floats here re-opens the F-SURF-01 mismatch.
- **Don't suppress honest degeneracy.** SVI railing on 1–5 day slices is real math on thin input;
  flag it so it disappears naturally once longer maturities arrive — don't hard-code it away.
- **File ownership.** This lane = `projection.py`, `serializers.py`, surface diagnostics. The
  capture/selection lane = `cp_rest_close_capture.py`, `chain_planning.py`. Claim on the TASKBOARD;
  do not cross.
- **uv only.**
