# infra-surface-fit-quality — why the SVI surface rails (and the nappe goes weird)

**Owner:** Matthieu · **Lane:** `infra-`/`core-` (capture call params + computing params) · **Priority:** P1
(blocks a trustworthy front; surfaced by the 2026-06-15 live SX5E validation run)

> **STATUS UPDATE (2026-06-17 board audit) — lane-1 groundwork PARTLY LANDED via `f1a6205` (ADR-0052).**
> Typed quote-QC floors now exist: `max_spread_pct` (`platform_config.py:245`, `qc.yaml:9`=0.05),
> `max_quote_age_seconds` (`:246`, `qc.yaml:10`=30.0); `min_points_per_slice` is typed config
> (`platform_config.py:301`, default 5, `ge=5`) with routing tests; `check_put_call_iv_spread` exists.
> The flag-not-reject `fit_slice` behaviour is confirmed correct (blueprint-prescribed) and lane-3
> (owner-gated reroute) correctly stays NOT done. **Still open:** the ρ-bound decision (still the
> aggressive `[-0.999, 0.999]` in `pricing.yaml:26` — relax/justify), the duplicated-`0.0`-delta-axis
> de-dup + the 108%/140% IV-point fix, and the required **settled-close re-validation findings doc**
> confirming `surface_fit_error` critical clears. Lane 2 (front robustness) re-homes onto the reading model.
>
> **LANE 1 + 3-DIAGNOSIS LANDED (2026-06-17, A2).** Findings + evidence in
> [infra-surface-fit-quality-findings](infra-surface-fit-quality-findings.md). Root cause: the dominant
> QC failure was a **benign `a_lower` false positive** (svi_a→0 with positive w_min on low-variance
> slices), NOT a bad fit — reproduces on the settled close, not an intraday confound. Fix (config-first):
> `check_surface_fit_error` treats `a_lower` as benign when the SVI minimum total variance is positive
> (still logged, no longer FAILs); served `0.0`-delta axis de-duped at the BFF (`atm`/`atmp` straddle
> pillars), `atmp` retained in `points`; ρ-bound KEPT `[-0.999, 0.999]` with an evidence note in
> `pricing.yaml`; `min_points_per_slice` deliberately left at 5 (rail is maturity-driven, not point-count
> driven). Settled-close SX5E `surface_fit_error` fail rate **93 % → 26 %**, residual concentrated in the
> genuinely-thin ultra-short front. The 105 %/82 % IV spikes are the 10d-wing extrapolation → **lane 2
> (front, A6)**.

> **3-onglets home (2026-06-17):** the "Market-tab" surface is now **Onglet 1 (Données) › ② NAPPE 3D
> + ③ Panneau Ténor** ([frontend-3onglets-target-ux](frontend-3onglets-target-ux.md)); lane 2 (front
> robustness to degenerate slices) re-homes onto that reading model, not the retired term-structure panels.

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

## Root cause + blueprint ruling — CORRECTED (2026-06-15, read the blueprint exactly)

The slices rail because **bad intraday quotes** reach the fit. A first instinct — gate `fit_slice`
to route a railed/arb/non-converged SVI to the nonparametric fallback — was **prototyped and
reverted**: read exactly, the blueprint does **not** prescribe that as the immediate fix. The
blueprint's surface pseudocode (§04 "Surface engine pseudocode") is `group_by_maturity` →
`fit_svi_or_fallback` with **no surface-level outlier rejection** (the `reject_outliers(method=
"mad")` is in the *forward* pseudocode), and our `actor/driver.py:749` matches it. And the
failure-mode table (§04.H) rules the current behaviour correct:

> *Surface fit failure | pathological quote set | **Publish fail flag and retain raw points** |
> [longer-term] Improve fallback interpolation path*

So **flag-not-reject is the blueprint-prescribed surface behaviour** — `fit_slice` serving the
railed SVI with `bound_hits` flagged + QC `surface_fit_error=fail` + raw points retained is correct,
**not a deviation**. The blueprint puts the lever **upstream**: §05-math-notes:36 *"improvements come
from better QC rather than a more complicated surface model"*; §12 *"bound parameters and **log**
bound hits"*; the fallback is *"for **sparse** slices"* (§12/§16); arb is *"diagnostics now,
enforcement later"* (§02:113). Routing a railed *dense* slice to the fallback is the **longer-term
"improve fallback interpolation path"** column — owner-gated, not the immediate fix.

**So the fix, per the blueprint, is three lanes — none of them `fit_slice`:**
1. **Upstream data hygiene / QC (the primary lever)** — tighten the quote QC (spread, quote-age),
   the IV-solver no-arb bounds, and strike selection / outlier thresholds (§04.H "tighten outlier
   thresholds") so bad intraday points never become IV points the SVI rails on. **Validate on
   settled-close data**, not intraday (the intraday thinness is a confound).
2. **Front robustness** — the front must render the flagged degenerate slice legibly (clamp the
   colour/Z scale, mark/exclude the outlier points), independent of the data. → **re-homes onto the
   Onglet-1 nappe** ([frontend-3onglets-target-ux](frontend-3onglets-target-ux.md) ② NAPPE 3D).
   **Confirmed live 2026-06-16 (audit F3):** the ~2-3d ultra-short slice serves IV spikes to **1.0–1.4**
   — the nappe must exclude/mark those outlier points (beyond the ~0.35 ceiling clamp). This is the
   blueprint-conform fix (flag-not-reject) — **NOT** a persist-side drop of the slice.
3. **Longer-term, owner-gated** — "improve fallback interpolation path" (route a railed dense slice
   to the smooth flagged fallback). A real blueprint item, but explicitly the *future* column; do
   not land it as the immediate fix.

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

Depends on / informs [[frontend-page1-cdc-buildout]] phase 7 (the front must ALSO survive a
degenerate slice gracefully, independently of this fix — lane 2 above). Related: `delta-window-fix`,
the `core-pricing-config-completeness` surface model/fallback typing (ADR 0028).

## Tech-lead assessment (Surface & Analytics family, 2026-06-17) — no backend work left
Re-confirmed against the landed state and the findings doc:
- **Lane 1 (upstream QC/data hygiene) — LANDED** (`cc28426`/`f1a6205`, findings doc); the benign
  `a_lower` false positive is fixed in QC, the served `0.0`-delta axis de-duped, ρ-bound kept with
  evidence. Settled-close SX5E fail rate 93%→26%, residual genuinely-thin ultra-short only.
- **Lane 2 (front robustness — nappe clamp) — FRONTEND, OUT OF SCOPE** for this family. It landed
  separately as `c894755` (Onglet-1 ③) under owner-owned `apps/frontend/**`.
- **Lane 3 (fallback routing) — OWNER-GATED** (blueprint "future" column, explicitly not the
  immediate fix).
Verdict: **owner-domain / gated — nothing for this family to land.**
