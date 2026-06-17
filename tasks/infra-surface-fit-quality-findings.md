# infra-surface-fit-quality — findings (settled-close re-validation)

**Worker:** A2 (stream A) · **Date:** 2026-06-17 · **Status:** landed (config-first + QC fix)

This is the findings half of [infra-surface-fit-quality](infra-surface-fit-quality.md). It attributes
the "weird nappe" / SVI rail to a root cause, with evidence from real settled-close data and the
blueprint reference, and records the landed fix plus the re-validation.

## TL;DR

The dominant QC failure was a **false positive, not a bad fit**. On the 2026-06-16 settled close,
`surface_fit_error` failed **93 % of SX5E slices** — 523/630 of them on a single bound hit, `a_lower`
(`svi_a` pinned to its `0.0` floor). Those fits are well-formed: tiny RMSE (1e-5…1e-3), a positive
minimum total variance, and a sane ATM vol (~13.5 % at 10d). `a→0` is a **parametrization sink**, not
a degeneracy: the SVI level can be carried by `a` OR by the wing-vertex term `b·σ·√(1−ρ²)`, and on a
low-variance (ultra-short) slice the optimizer chooses the latter and drives `a` to its floor, leaving
the curve unchanged.

The fix is **upstream, in the QC** (the blueprint's prescribed lever — §05-math-notes:36 "improvements
come from better QC", §12:70 "bound parameters and log bound hits"): `check_surface_fit_error` now
treats an `a_lower` bound-hit as **benign when the reconstructed minimum total variance is positive** —
it stays *logged* (per §12:70) but no longer flips QC to FAIL. After the fix the SX5E settled-close QC
fail rate drops **93 % → 26 %**, and the residual failures are **genuine** (rho rails / arb breaches /
non-convergence) concentrated in the **ultra-short bucket** the blueprint already treats as
low-confidence. `fit_slice` is untouched (flag-not-reject stays — blueprint §04.H).

## Attributed root cause

| Symptom | Cause | Evidence | Verdict |
|---|---|---|---|
| `surface_fit_error` fails ~90 % of slices, almost all `bound_hit`, RMSE tiny | **Benign `a_lower` false positive.** `svi_a→0` with positive `w_min`; level absorbed by the wing term | 2026-06-16 SX5E: 523/630 slices `a_lower`, `min_total_variance>0`, ATM IV sane; synthetic repro: a clean smile with true `a=0.008` recovers `a≈0.008` (no rail), but a 10d smile whose true `w_min<b·σ·√(1−ρ²)` rails `a→0` with RMSE 1e-17 | **QC bug, not a fit/nappe defect.** Fixed in QC. |
| `svi_rho → −0.999` rails (the "railed parameter") | **Ultra-short underdetermination** (and, where arb breaches, a genuine pathology) | 2026-06-16 SX5E ρ-rails cluster at T<0.038y (2d…14d); the 2d/3d ones are NOT arb-free; the longer ρ-rails are arb-free extreme skew. Liquid core (T>0.25y) does not rail ρ | **Real (ultra-short).** Arb-breaching ρ-rails correctly FAIL (arb); arb-free extreme-skew rails stay logged. ρ bound KEPT. |
| Absurd IV points (105 %/82 % at deep-put deltas) | **Steep-wing extrapolation of the ultra-short slice** | 2026-06-16: the only >60 % IVs are 10d (T=0.027y) at −6Δ/−8Δ. The 1m–18m tenors are all 12–32 %. 2026-06-15 had no 10d tenor → no spikes (the task's 108/140 % were a different intraday slice) | **Downstream of the ultra-short fit**, not a grid bug. The nappe clamp/exclusion is **lane 2 (front, A6)**. |
| Duplicated `0.0` delta in the served axis | **The `atm`/`atmp` straddle pillars** (both `target_delta=0.0`) flowing straight into `smile.deltas` | `projection.delta_band_axis` emits `("atm","atmp")` at `0.0` by design (`8f71fb5`, the ATM-put leg the straddle composes from). The serving axis carried both | **Serving-axis artifact**, not a projection/grid bug. De-duped at the BFF smile axis; `atmp` retained in `points`. |

### Is it intraday or settled-close?

**Both the `a_lower` flood and the ρ rails reproduce on the SETTLED-CLOSE 2026-06-16 capture**
(snapshot stamped 17:30 CET, the OESX settlement instant) — so the railing is **NOT** an intraday-
thinness confound. The 2026-06-15 run was thin (9 SX5E slices, no 10d tenor); 2026-06-16 was the full
basket (630 SX5E slices). The cause survives the close, which is why the fix is a QC/config fix, not a
"wait for the close" non-fix.

> Data note: the 2026-06-16 derived partitions were re-derived by the concurrent
> `T-clean-ingestion-2026-06-16` cleanup while this work was in flight. The settled-close evidence above
> was read from the preserved copy at `data/_provisional_archive/2026-06-16-pre-cleanup/` (and
> `data/_rebuild_backups/2026-06-16/`). No canonical store was written to during diagnosis.

## The fix (landed)

1. **QC: benign-`a_floor` discrimination (the primary lever).**
   - `SviParams.minimum_total_variance()` (`surfaces/svi.py`) — the SVI vertex value `a + b·σ·√(1−ρ²)`.
   - `is_benign_a_floor()` + `degeneracy_reasons(..., minimum_total_variance=)` (`surfaces/fit.py`) —
     an `a_lower` bound-hit is filtered when `w_min>0`. Shared by QC and the serializer.
   - `check_surface_fit_error` (`qc/checks.py`) — computes `w_min` from `fit.svi`, separates
     `benign_bound_hits` from `genuine_bound_hits`, fails only on the genuine ones. Both are kept in the
     QC context for audit (blueprint §12:70 "log bound hits").
   - `serializers.surface_parameters_to_dict` — the front's `degenerate` flag stops false-flagging too.
2. **Served-axis de-dup** (`routers/analytics.py`) — `_smile_axis_cells` collapses cells that share a
   `target_delta` on the `smile` plotting axis (the `atm`/`atmp` pair). `points` keeps every cell so the
   ATM straddle stays composable downstream.
3. **ρ-bound decision: KEPT `[-0.999, 0.999]`, justified** (see the `pricing.yaml` note). Relaxing is
   neither possible (|ρ|=1 makes the SVI ill-defined) nor the lever (rails are ultra-short, not bound-
   width driven; the liquid core does not rail ρ).
4. **`min_points_per_slice`: left at 5, deliberately.** Raising it was considered and rejected on the
   data: the ρ-rail rate is only weakly point-count driven (38 % at 8–9 points, 17 % at 15+), so a
   higher floor would route fittable slices to the fallback without cleanly killing the rail. The rail is
   **maturity-driven (ultra-short)**, already handled by the `ultra_short_maturity_years` regime in
   `qc.yaml`. Changing the floor would be cargo-culting.

`fit_slice` is **not** gated (flag-not-reject is blueprint-prescribed, §04.H; the reroute prototype was
reverted in `dbc05c6`).

## Settled-close re-validation (the acceptance bar)

`check_surface_fit_error` re-run over the real persisted slices with the shipped `configs/` thresholds:

```
2026-06-15 (live)        [SX5E]:   9 slices | OLD fail 8 (88%)  → NEW fail 1 (11%)
2026-06-16 (settled)     [SX5E]: 630 slices | OLD fail 590 (93%) → NEW fail 164 (26%)
2026-06-16 (settled)     [ALL] : 2321 slices| OLD fail 1916 (82%)→ NEW fail 1169 (50%)
```

Residual SX5E settled-close failures by maturity bucket (the failures that remain are genuine):

```
ultra_short (<0.038y, ~2wk): 91/124 fail (73%)   ← genuine: thin/noisy front, rho rails + arb breaches
short       (0.038–0.25y)  : 30/277 fail (10%)
core        (>0.25y)       : 43/229 fail (18%)
```

**Verdict: the `surface_fit_error` critical clears on the liquid surface** (the benign `a_lower` flood is
gone) **and honestly flags only the genuinely-thin ultra-short front tenors**, exactly as the acceptance
criterion asks. The remaining ultra-short failures are real (arb-breaching or rho-railed near-the-front
weeklies) and the blueprint already classes that regime low-confidence; their *front* handling is lane 2.

## Gate

```
uv run ruff check .   → All checks passed!
uv run mypy .         → Success: no issues found in 268 source files
uv run lint-imports   → Contracts: 2 kept, 0 broken
uv run pytest -q      → 2456 passed, 12 skipped
```

## Hand-offs

- **Lane 2 (front robustness)** is A6's — the nappe must clamp/exclude the ultra-short 10d wing spikes
  (IV→1.0+). Independent of this fix.
- **Lane 3 (owner-gated fallback routing)** stays NOT done (blueprint "future" column).
