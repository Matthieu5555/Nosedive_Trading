# infra-surface-fit-quality — why the SVI surface rails (and the nappe goes weird)

**Owner:** Matthieu · **Lane:** `infra-`/`core-` (capture call params + computing params) · **Priority:** P1
(blocks a trustworthy front; surfaced by the 2026-06-15 live SX5E validation run)

## Symptom (measured on real 2026-06-15 intraday data)

The Market-tab vol surface renders **distorted/spiky** and everything downstream of it (greeks
term-structure, per-maturity greeks) is degenerate. Root, confirmed in the data:

- QC `surface_fit_error` **fails 31/58 slices**, almost all `bound_hit` (26) — the SVI optimizer
  **rails a parameter to its bound** (`svi_rho = -0.9989999…` = pinned to the −0.999 bound), a few
  `arb_violation`. **RMSE itself is tiny** (1e-5…3e-3) — it is *over-fit on a railed parameter*,
  not a high-error fit (the `check_surface_fit_error` flag-not-reject path, `qc/checks.py:447`).
- The served smile (`/api/analytics?underlying=SX5E&trade_date=2026-06-15`) carries **absurd IV
  points** (e.g. 108%, 140% at deltas −0.14/−0.12 on the 10d) and **duplicated `0.0` deltas** in
  the axis — both feed garbage into every downstream viz.
- Worst on SX5E (10 slices), ENR (7), then SIE/SAP — i.e. NOT only the thinnest names.

## Root cause — CONFIRMED (2026-06-15) + blueprint ruling

`fit_slice` (`infra/surfaces/fit.py:164`) routes **on point-count alone**: `if len(ks) >=
config.min_points_per_slice:` → commit to SVI and **return it even when it railed** (`bound_hits`),
**violated arbitrage** (`butterfly_violations`), or **did not converge**. Those are recorded as
*diagnostics* but never trigger the fallback. So a slice with enough points but bad/thin data keeps
a **railed SVI** (e.g. `svi_rho = −0.999` pinned to its bound → the 108%/140% IV spikes) and serves
it to the nappe.

**The blueprint already rules this a deviation** (`docs/blueprint/05-math-notes.md:38`): *"In sparse
maturities, **prefer a conservative fallback that is smooth and flagged over an aggressive
calibration that produces sharp but unreliable local features.**"* and (:36) *"improvements come from
**better QC rather than from a more complicated surface model**"*. §02-math-framework.md:113 mandates
the SVI **+ nonparametric fallback in total-variance space**. The railed SVI is precisely the
"aggressive calibration with sharp unreliable local features" the blueprint says to reject. This is
state-of-the-art too (Gatheral/SSVI: never serve a non-arb-free / railed slice).

**The fix is therefore NOT a fancier model.** Route `fit_slice` to the nonparametric fallback when
the SVI fit is **railed / arb-violating / non-converged**, not only when points are too few — i.e.
make `arb_free`/`bound_hits`/`converged` *gate* the method choice, not just annotate it. Keep it in
total-variance space; keep the slice flagged. Then a thin/bad slice renders smooth-and-flagged, not
spiky.

## Remaining to verify on settled-close data (the capture-params half)



Is this **intraday incompleteness** (too few/too noisy strikes mid-session → SVI underdetermined →
rails; clears at the settled close) **or** a real **fit-config / capture-params** issue that would
rail at the close too? Audit both halves **against the blueprint** (`docs/blueprint/`,
`vol-surface-pedagogique`, the cahier) — they specify the intended fit:

1. **Capture call params** (`configs/{universe,qc}.yaml`, the discovery window): how many strikes
   per tenor actually land? Is the ±30Δ / `band_step` window wide/dense enough that each SVI slice
   has enough points to be determined? (`min_strikes_per_side`, the delta-window — see
   [[delta-window-fix]].) A slice with too few points is the classic rail cause.
2. **Computing params** (`configs/pricing.yaml` surface block, the SVI fit): the SVI parameter
   **bounds** (is −0.999 ρ too aggressive a clamp?), the **seed** (single vol seed — see
   [[delta-window-fix]]), `SurfaceConfig.min_points_per_slice` (the SVI-trust routing threshold —
   below it, fall back to the nonparametric fit instead of a railed SVI), and the arb-repair pass.
   Does the blueprint prescribe a seed/bounds/fallback policy we deviate from?
3. **The duplicated `0.0` delta** and the 108%/140% IV points: are these a projection/grid bug
   (two cells at delta 0.0) or a downstream of the railed fit? Trace `projection.py` /
   `surface_grid` build.

## Acceptance

- A short findings doc: railing cause attributed to (intraday | capture-window | SVI bounds/seed |
  routing | grid bug), each with evidence from the 2026-06-15 data + the blueprint reference.
- The fix (config-first where possible): widen the discovery window / raise `min_points_per_slice`
  so thin slices fall back to the flagged nonparametric fit instead of a railed SVI; relax/justify
  the ρ bound; de-dup the delta axis. Re-run a **settled-close** capture and confirm
  `surface_fit_error` critical clears (or honestly flags only genuinely-thin far tenors).
- Coordinate with the close: re-validate against a real 18:15+ capture, not intraday.

## Links

Depends on / informs [[frontend-page-a-robustness-audit]] (the front must ALSO survive a degenerate
slice gracefully, independently of this fix). Related: `delta-window-fix`, the
`core-pricing-config-completeness` surface model/fallback typing (ADR 0028).
