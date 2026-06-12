# Remediation worklist — intent-vs-delivery audit (2026-06-12)

Single ordered worklist for the [2026-06-12 intent-vs-delivery audit](AUDIT-INTENT-VS-DELIVERY-2026-06-12.md)
(the "green gate ≠ correct output" class). Owner context folded in (2026-06-12): severities
corrected, landed/in-progress work excluded, latent mines separated from active bugs.

## Already covered — do NOT re-open

| Audit finding | Status | Where |
|---|---|---|
| Cap-1 tenor grid (front-month only, 1m…3y empty) | ✅ **LANDED** `74d2cc7` | [T-tenor-selection](T-tenor-selection.md) — **needs a re-capture to bank real 1m…3y** (operational, not a code task) |
| Cap-2 delta band (±16-strike clip of the 30Δ band) | ✅ **LANDED** (gate 1404/0/16, 2026-06-12) | [T-delta-window](T-delta-window.md) — `_DISCOVERY_STRIKES_PER_SIDE` removed; `discovery_delta_bound`/`select_discovery_strikes` reuse `select_strikes_delta_band` (√T-scaling superset); `discovery_working_vol=0.40` typed config + `DiscoveryRunawayError` fail-loud valve. Owner ruling: full-30Δ, **cap=None**, single vol seed. Universe config-hash golden regenerated (pre-capture). **Awaits owner-gated re-capture — do not re-capture.** |
| F-SURF-01 / SVI degeneracy *propagation* / F-BFF-03/04 | ✅ **LANDED** | [T-vol-surface-correctness](T-vol-surface-correctness.md) — DF interp at pinned tenor, `arb_free`/`bound_hits` propagated, holes labelled |
| "Read the QC" (verdicts were unread) | ✅ **LANDED** | [T-capture-coverage-panel](T-capture-coverage-panel.md) — `/api/coverage` `f140a0f` + `CoverageTable`/`CoveragePanel` `d972295`; only Market.tsx placement left |
| QC-1 / St-1 — projected-grid holes invisible (`persist_outputs` silent-skip, gaps dropped) | 📋 **EXISTING TASK** | maps to **ADR-0040 [T-raw-invariant](T-raw-invariant.md)** #3/#4 (complete-or-flagged) — fold there, do not create a new task |

## Active bugs (deliver wrong results / mask QC signal today)

| # | Task | Finding | Sev | Why it bites now |
|---|------|---------|-----|------------------|
| **1** | ✅ **LANDED** [T-qc-residual-units](T-qc-residual-units.md) | An-1 / An-2 / QC-4 | **HIGH** | Absolute-$ forward/parity thresholds on a 7400-pt index ⇒ permanent FAIL even on good slices (2.48 vs 0.10). **Masks real QC health and pollutes the coverage panel.** A re-capture will NOT fix it — it's units. Folds An-1 (label rel-vs-abs split → one residual basis). **Fixed 2026-06-12 (`audit-fixes-batch1`):** `max_residual_mad`/`max_parity_residual` → `max_rel_residual_mad`(0.01)/`max_rel_parity_residual`(0.02), normalized by `forward` inside the checks (no caller change); `measured_value` now relative; qc bundle hash regenerated (`c660e955…`); index-scale PASS/FAIL tests added. Gate **1414/0/16**, ruff/mypy/lint-imports clean. |
| **2** | [T-surface-arbfree-qc](T-surface-arbfree-qc.md) | An-3 / QC-2 (seed #3) | **HIGH** | `surface_fit_error` gates on RMSE only (`checks.py:384`); the `arb_free`/`bound_hits` flags the vol-surface lane *propagated* are not consumed by the gate → 3/4 SPX slices `arb_free:False` (rho railed, sigma→0) still PASS. The propagation landed; the **gate half is open**. |

## Latent mines (not driving live behaviour — fix before a refactor arms them)

| # | Task | Finding | Sev | Why latent |
|---|------|---------|-----|------------|
| **3** | [T-capture-config-coherence](T-capture-config-coherence.md) | Cap-3 / Lane-0 | MED-HIGH | `capture.yaml` (`n_expiries:4/min_days:25/max_days:90`) contradicts the `10d…3y` grid but is **not read** by the live path. Misleads readers / legacy collector. Folds the **cosmetic** stale `universe.underlyings:[AAPL,MSFT,SPY]` (owner-verified **log-only** at `jobs.py:128`, downgraded from HIGH). |
| **4** | [T-scenario-rate-axis](T-scenario-rate-axis.md) | Rk-1 | MED | `scenarios.yaml` has no rate-shock axis (course: rate ±~10%). Not verified on delivered data (no 06-11 risk partition). |
| **5** | [T-pricing-config-completeness](T-pricing-config-completeness.md) | An-4 / Lane-0 | MED | `pricing.yaml` missing `model/fallback_model/min_points_per_slice` + equity `forward_engine` block — intent lives as `.py` literals (ADR-0028 gap). Correct today, no golden to catch drift. |
| **6** | [T-strike-window-pct-clip](T-strike-window-pct-clip.md) | owner hand-off after T-delta-window (2026-06-12) | MED | **Second** request-shaping bound on the capture path: the %-of-spot fallback `select_strikes` (`strike_window_pct=0.35`, a `.py` literal at `chain_planning.py:90`) can silently clip the 30Δ band **at σ≳0.23 / 3y** (band > ±35%). Harmless at realistic vols (band ≤±18.5% at the 0.40 seed) so left as-is by the delta-window fix. Backstop = `delta_band_completeness` QC (end-to-end), but the bound is neither typed nor delivery-tested. |

## Minor / deferred (noted, no separate file yet)

- **QC-3** `qc.yaml:37 max_delta_step: 0.25` is half the full ±0.30 band — a loose completeness bar
  ({−0.30,+0.30} with no interior could nearly pass). Tighten once [T-delta-window](T-delta-window.md)
  lands and the 8-point band is actually delivered. (LOW-MED)
- **QC-5** no QC validator asserts the $-Greek monetization convention or stress-grid coverage.
  Greek unit *labels* are present and correct (An-5 clean) — this is a missing guard, not a bug. (LOW)

## Coverage caveats (from the audit, not silent)

- **Risk delivered-data unverified** — no `scenario_results`/`risk_aggregates` partition for
  2026-06-11; Lane 4 (incl. Rk-1) is config-and-code only.
- **Saxo/Deribit** read in Lane 0 (forward block MATCHes blueprint) but produced no 06-11 data
  (IBKR-only capture) — config-drift only.
- `calendar_sanity` failing is the **ultra-short-slice symptom** of the tenor root, expected to clear
  after a real-term-structure re-capture — explicitly **not** in the units bucket (#1).
