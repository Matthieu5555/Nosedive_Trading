# T-intent-vs-delivery-audit — findings (2026-06-16)

Audit of the "green gate ≠ correct output" failure class defined in
`tasks/platform-intent-vs-delivery-audit.md`: an economic policy is expressed in config,
a separate technical bound silently clips or overrides it, and the gate stays green because
tests check the *mechanism* on a synthetic fixture rather than the *delivered economic result
on real captured data*.

**Method.** Three lane agents (capture/storage, analytics, QC/risk) traced each policy in
`TARGET.md` + `configs/` to code and the four method questions. Every load-bearing candidate
was then verified by the lead against the on-disk 2026-06-16 partitions and the QC results
(`data/qc/qc_results/trade_date=2026-06-16`) as the oracle. The live CP Gateway was **not**
reachable (`IBKR_CP_GATEWAY=unset`), so the banked partitions + QC stand in for it; no
capture or write was triggered (read-only, prod slot untouched).

**Headline.** The single most useful result of this pass is a *negative* one that the data
forced: the obvious "the term structure is truncated, capture must be clipping it" reading is
**wrong on the real data**. SX5E captured out to 3y; the 2y/3y slices are simply not two-sided
at the close. The real defects are quieter (a *latent* nearest-N regression, a mis-calibrated
long-end QC floor). This is precisely the trap the brief warns about — the prior 101-finding
audit "missed the roots" by reading code, not data.

---

## Confirmed findings

| # | Area | File:line | Intended policy (source) | The clip / override | Why tests stay green | QC catches it? | Severity | Suggested remediation task |
|---|------|-----------|--------------------------|---------------------|----------------------|----------------|----------|----------------------------|
| F1 | Capture / selection | `infra-ibkr/.../collectors/cp_rest_close_capture.py:633-642` → `infra/.../universe/chain_planning.py:118-120,348-353,402-430` | Pinned tenor grid 10d…3y; commit `38910d9` (today) intends "keep EVERY listed maturity, full term structure out to the longest LEAP ~9.5y" | `_selection_from_config` builds `ChainSelection` with **only** `max_expiries=_CAPTURE_ALL_MATURITIES_BUDGET=64`, leaving `tenor_years`/`as_of` empty → `is_tenor_targeted()` False → both paths fall to the nearest-N slice `select_expiries → unique[:64]` / `_nearest_expiries(group, 64)`. `38910d9` **reverted** the tenor-bracketing wiring that the tenor-seed fix `74d2cc7` had added. The "64 is far above any real chain" rationale is unverified: a full weekly chain (SPX, or a weekly-heavy SX5E day) exceeds 64, and nearest-N is the wrong primitive for "keep the long end" — at any finite budget it front-loads weeklies and drops the LEAPs. **Latent on this SX5E day (29<64), would bite on SPX.** | Tenor tests (`test_tenor_selection`) use hand-built chains ≤8 expiries; the bracketing path is exercised in isolation, never the production `_selection_from_config` default, and never a >64-expiry real chain. Same shape as seed #1. | `tenor_coverage_floor` — yes, would flag; **unread** | **P1** (latent; **P0** the first time it runs against a chain with >64 listed expiries) | Re-wire tenor-targeted bracketing into `_selection_from_config`, OR if "keep everything" is the intent, switch the primitive from nearest-N to keep-all/bracket and prove the budget exceeds the longest real chain on SPX. Add a delivered-result test on a >64-expiry chain. |
| F2 | Capture vs QC (long-end coverage) | `configs/qc.yaml:22-30` (`tenor_floors` 2y:5, 3y:5) vs banked data | Grid demands ≥5 usable points at every pinned tenor incl. 2y/3y | **Not a code clip — a floor mis-calibrated against market liquidity.** Verified: SX5E 2026-06-16 captured to 2029-06, but `2028-06-16`(2y), `2029-03-16`, `2029-06-15`(3y) return **last-only** (`completeness=0.333`, two-sided rows = 0). They are correctly dropped before fit, so the projected surface tops at 2028-03 (~1.75y). The floor then demands two-sided points the market never quotes for LEAPs. | QC tests use synthetic 3-tenor fixtures at floor=3; no real LEAP slice with zero two-sided quotes. | `tenor_coverage_floor` — fails, but the failure is **structural (illiquid LEAPs), not a fixable capture defect** | **P1** | A long-end tenor policy (flag / down-weight / relax the floor where two-sided LEAP quotes are structurally absent), the long-end twin of the open ultra-short-slice policy. Cousin of the QC-floor-recalibration in ADR 0052. |
| F3 | QC (meta blind-spot residual) | `infra/.../qc/checks.py:356-364,383` | A slice is usable only if RMSE OK **and** arb-free / converged | The status now correctly ANDs `not degeneracy_reasons` (the original blind spot is **closed**), but the emitted `measured_value` is still `fit.rmse`. On a railed ultra-short SVI the FAIL carries a tiny "good-looking" RMSE — a consumer (coverage panel, triage) reading `measured_value` alone sees the opposite of the status. | `test_surface_fit_fails_arb_violation_despite_tiny_rmse` asserts status FAIL but does not assert the reported value reflects the *reason*; synthetic only. | self (the check is the subject) | **P2** | When degeneracy drives the FAIL, emit a degeneracy/arb-aligned `measured_value` (e.g. arb_free as 0/1) so value and status agree. |
| F4 | Analytics (forward) | `infra/.../forwards/estimate.py` `_cap_candidates` + `configs/pricing.yaml:51` (`max_candidate_count: null`) | Use all valid parity pairs (null = uncapped) | Cap site exists; flipping the config to an int silently keeps the top-N most-liquid pairs and drops wing pairs, which can bias the forward. **Intent met today (null)** — this is a latent foot-gun + test gap. | `test_max_candidate_count_keeps_the_most_liquid_pairs` checks the *cap mechanism*, not whether capping changes the forward vs uncapped on real data. | `forward_stability` scores residual MAD of the *capped* fit, not the bias | **P2** | Add a delivered-result test: cap vs no-cap forward on a real chain; assert the cap does not move the forward beyond tolerance. Keep `null` in prod. |
| F5 | Risk (scenarios) | `infra/.../risk/scenarios.py:150,152`; `infra/.../risk/stress_surface.py:112` | Shocks are economic moves from `configs/scenarios.yaml`; pricing must stay physical | `new_vol = max(vol + vol_shock, 0.0)` silently floors vol to 0; `new_rate = rate + rate_shock` is unbounded (can go negative); `shocked_forward = forward*(1+spot_shock)` can go negative for shock < −1. No feasibility gate; a clipped/negative state prices to a degenerate-but-non-erroring number. **Latent** — does not trigger under the configured ±10% spot / ±5pt vol / ±25bp rate on a 7400-pt index, but a small-vol position or a wider shock set would. | `test_scenario` uses configured-range shocks on large forwards; never a small-vol position or a >100% spot shock. | none (no scenario-feasibility QC) | **P2** | Add a `scenario_feasibility` pre-check (flag positions a shock would clip / send a forward ≤0) and explicit bounds in `scenarios.yaml`; prefer fail-loud over silent clip. |
| F6 | Config-as-literal (ADR 0028) | `infra-ibkr/.../cp_rest_snapshot.py:36` (`SNAPSHOT_MAX_CONIDS=50`); `infra-ibkr/.../cp_rest_discovery_cache.py:27` (`TRSRV_SECDEF_BATCH=200`); `infra/.../risk/account_reconciliation.py:50` (`DEFAULT_ACCOUNT_RECON_TOLERANCE`) | ADR 0028: no business/operational parameter as a `.py` literal | Pacing batch sizes and the recon tolerance live as module literals, not typed config. The two batch sizes are pacing (scope unchanged) so low-risk; the **recon tolerance is economic** — it decides which book/ledger breach passes silently (cash_abs 1e-2). | Tests use the literal default; no override path exercised. | n/a | **P2** | Move the recon tolerance to typed config (own block) with an override param; document the two batch sizes' broker basis (CP Gateway rate limit / URI length) and move them to `broker.yaml`. |
| F7 | Storage / contracts (observability) | `infra/.../storage/adapter.py:107-124,370-399` | Run-partitioned tables keep "latest run wins"; `append_only` tables refuse dup keys, versioned tables overwrite | Both behaviours are correct but **silent**: a re-capture (e.g. a morning re-run after a broker hiccup) overwrites the prior run with no log, no version branch, no marker on the row recording which path was taken. A silently different re-capture is invisible. | Storage tested in isolation; no "two runs same day" integration test asserting grid stability. | none (QC runs per-run, can't see a silent re-capture) | **P2** | Emit an audit log line when a versioned/latest-wins table is overwritten; surface `run_id` into QC results so re-captures are visible. |

---

## Checked and found clean (legible coverage)

Two of these are **overturned false positives** from the lane agents — recorded so the
coverage is honest, not implied:

- **Forward `quality_label` vs QC `forward_stability` threshold — NOT a finding.** The
  QC/risk lane flagged a "10× looser" mismatch (label "good" at 1e-3 vs QC pass at 1e-2). It
  compared against the wrong tier: `configs/qc.yaml:59-61` explicitly aligns
  `max_rel_residual_mad` to the forward engine's **`fair_rel_residual` (1e-2)** so that
  "poor label ⇔ QC fail" by design. `good` is a finer diagnostic tier above the QC gate, not
  a contradiction. Aligned and documented.
- **Constituent-option capture still live — NOT a live finding.** The on-disk
  `surface_fit_error` QC carries 40+ constituent `target_key`s (`ASML@…`, `BMW@…`), which
  looks like an ADR-0051 scope breach. Checked the clock, not the label: that QC run is
  **15:05 today**, before tonight's ADR-0051 amputation landed (commits through 23:38). The
  data is pre-amputation residue, not a current code path. **Verify it clears on the next
  re-capture** rather than treating it as a defect.

Verified aligned (intent ⇒ delivery), no clip:

- `select_expiries_bracketing` / `bracket_dates` / `tenor_target_dates` — correct in
  isolation (the gap is the wiring, F1, not the logic).
- Delta band: `select_strikes_delta_band` driven by `delta_bound` + `min_strikes_per_side`
  (both config); the `_DISCOVERY_STRIKES_PER_SIDE` seed is already fixed (delta-driven now).
- Day-count (ACT/365 etc.) applied consistently across forward, DF, and Greeks.
- Discount factor: log-linear interpolation, flat-rate extrapolation, `default_discount_factor`
  fallback — all three behaviours tested and delivered.
- Dollar Greeks units: `gamma_normalisation` / `theta_day_count` are config-driven, both
  forks tested. Tenor-grid pinning raises loudly on drift (no silent clip).
- Moneyness buckets emitted for every config bucket. `quote_integrity` floor
  (`min_two_sided_fraction`) is a hard floor, not a clip. `iv_solver_convergence`,
  `parity_residual` (numeric), `calendar_sanity`, `greek_sanity` thresholds all typed config,
  logic sound. Risk aggregation dimensions not hardcoded.

---

## Sampled / not deep-audited (no silent truncation)

- Replay / backtest position store — out of this pass's scope.
- Rates curve R1 (`infra-rates-curve-ingest`) and per-side surfaces R2 — in-flight; the seams
  aren't in place to host a clip yet.
- Alert delivery / kill-switch (§5.9) — not built.
- Vanna/Volga/Charm units — covered clean by the 2026-06-14 boundary-FD audit; not re-done.

---

## Suggested remediation tasks (one per confirmed finding)

- **`T-capture-tenor-bracket-rewire`** (F1, P1/latent-P0) — re-activate tenor bracketing in
  `_selection_from_config`, or prove + switch the keep-all primitive; delivered-result test on
  a >64-expiry chain.
- **`T-qc-long-end-tenor-policy`** (F2, P1) — long-end illiquid-LEAP policy mirroring the
  ultra-short-slice policy; recalibrate `tenor_floors` for 2y/3y against two-sided liquidity.
- **`T-qc-surface-fit-measured-value`** (F3, P2) — align the reported value with the failure
  reason when degeneracy drives the FAIL.
- **`T-forward-candidate-cap-guardtest`** (F4, P2) — cap-vs-uncap delivered-forward test.
- **`T-risk-scenario-feasibility-gate`** (F5, P2) — feasibility pre-check + config bounds,
  fail-loud over silent clip.
- **`T-config-recon-tolerance-and-batch-knobs`** (F6, P2) — move recon tolerance + batch
  sizes to typed config (ADR 0028).
- **`T-storage-recapture-visibility`** (F7, P2) — log overwrites, surface `run_id` to QC.

## What the pass establishes

The failure class is **largely closed at the seed sites** (tenor bracketing logic, delta
window, the `surface_fit_error` arb blind spot, the `max_delta_step` coarse-grid bug, relative
parity residuals, the quote-integrity floor are all landed). The live residue is one **latent
regression** (F1 — the bracketing got un-wired this morning) and a cluster of **mis-calibrated
QC floors / silent-but-physical clips** (F2, F3, F5) that only a *delivered-on-real-data* check
surfaces. The read-path fix the brief already names — `T-capture-coverage-panel` — is the right
home for making F1/F2 visible to operators instead of failing unread in the QC log.
