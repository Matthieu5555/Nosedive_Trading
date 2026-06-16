# Task — Return dispersion to the blueprint: realized-vol ρ̄, retire constituent-option capture

**Status:** ✅ DONE (2026-06-16) — full amputation landed, gate green (ruff `.` + lint-imports + mypy clean; pytest 2374/2374; web tsc + 152 vitest). Eq. 23 applied literally (full-membership ρ̄ on realized vol, `basket_size:null`); constituent-option capture lane + entitlement probe + `ConstituentCaptureOutcome` + `constituent_top_n`/`capture_pool_size` all removed; front coverage constituent-OPTION column dropped. `dispersion_top_n` (strategy book) + `discovery_pool_size` (index leg) kept. Residual owner choice (non-blocking): reconvert the front coverage panel to constituent-**price** coverage vs leave dropped. **Owner ruling set by Vincent (2026-06-15).**
**Decision of record:** [`ADR 0051`](../.agent/decisions/0051-return-to-blueprint-dispersion-realized-vol-diagnostic.md)
(supersedes 0045, partially 0044). Resolves OQ-12.
**Lane:** `infra-` (signal rewire) + `ibkr-` (retire the capture lane) + `core-` (drop the capture knob).

> ✅ **All gestes landed (2026-06-16).** The capture path is index-only
> (`live_basket_source.source` → `cp_rest_close_capture.collect_live_basket`; `store` backs only the
> discovery cache). ρ̄ reads the FULL membership's realized vol (`basket_size:null`, Eq. 23). The
> constituent-option capture lane, the entitlement pre-flight, `ConstituentCaptureOutcome`,
> `constituent_top_n` and `capture_pool_size` are all removed; the front coverage constituent-OPTION
> column is dropped. Gate green (ruff `.` + lint-imports + mypy clean; pytest 2374/2374; web tsc +
> 152 vitest). The per-geste checklist below is reconciled to ✅.

## Why

The constituent-option-capture lane (ADR 0045) baked a *strategy* choice into the *immutable* raw
layer — causing permanent option-history loss for un-captured names and the serial-capture
throughput crisis — for a feature the **blueprint computes from data we already hold**. Eq. 23
(`02-math-framework.md:129`) needs constituent **volatilities** + weights (or an average-correlation
assumption), explicitly a "diagnostics primitive, not strategy logic" — never constituent *implied*
vols, never captured constituent chains. The blueprint captures the index's **options** and the
underlyings' **prices**. See ADR 0051 for the full argument and the realized-vol caveat.

## Scope of change — the literal amputation checklist

**Landed 2026-06-16** by the coordinating agent (backend + goldens + front, one coherent change,
gate green). The per-geste status below is reconciled to the delivered state.

**Blueprint mandate (the literal rule).** Eq. 23 (`02-math-framework.md:127-133`, "Index or basket
variance identity") sums over **all** constituents `i` with weights `w_i` — it takes "a vector of
weights, constituent volatilities", a "diagnostics primitive, not strategy logic". There is **no
top-N anywhere** in the blueprint (`07-configuration`, `15-data-governance`, `09-data-dictionary`,
`05-math-notes` all checked clean). So ρ̄ is computed over the **full index membership**; any top-N
truncation of the ρ̄ basket violates the identity (σ_I² cannot reconstruct from a subset).

1. ✅ **`infra` — ρ̄ on realized constituent vol (DONE, coordinating agent).** `signal_set.py` feeds
   `implied_correlation`'s `constituent_vols` from `realized_vol_by_subject` (all names from
   `daily_bar`); `index_vol` stays the index implied ATM vol (hybrid ρ̄, Eq. 23). `test_signal_set.py`
   updated. Removes the implied-vol top-10 **source** bias.
2. ✅ **`infra`/`core` — full-membership ρ̄ basket (DONE).** `basket_size: null` in
   `configs/universe.yaml` (was `10`) so the ρ̄ basket is the **full membership** via
   `_resolve_basket` → `members(...)` (no `top_n_by_weight` truncation) — Eq. 23 honoured literally.
   `universe` config-hash golden regenerated (`test_config_core.py` oracle updated;
   section-isolated — only `universe`+composite moved). **`dispersion_top_n` (strategy book)
   untouched** — Eq. 23 does not govern it.
3. ✅ **Contracts / storage — `ConstituentCaptureOutcome` retired (DONE, coordinating agent).** Table
   + contract + registry row + fixtures removed; `contracts_plane_rows.json` regenerated (−1 row).
4. ✅ **`core` — `constituent_top_n` + `capture_pool_size` dropped (DONE, coordinating agent).** Both
   removed from `platform_config.py` (the capture gates). `discovery_pool_size` stays (index leg).
5. ✅ **`infra-ibkr` — dead capture machinery deleted (DONE).** Removed
   `collectors/cp_rest_constituent_capture.py` + its `collectors/__init__` exports +
   `test_cp_rest_constituent_capture.py` (its index-only contract test was preserved into
   `test_live_capture_spine.py`); removed the single-name **entitlement pre-flight**
   (`scripts/entitlement_probe.py`, `cp_rest_entitlement_probe.py`, its test). `history_backfill.py`
   + the index close-capture **unchanged**.
6. ✅ **`core` — `configs/universe.yaml` cleanup (DONE).** Stripped `constituent_top_n`,
   `capture_pool_size`, and the top-10 bias comments; `dispersion_top_n` comment corrected (book
   selector, not capture). `universe` golden regenerated once (folds in #2).
7. ✅ **Front — coverage panel (DONE — dropped).** The constituent-OPTION capture column was removed
   from `routers/coverage.py` + `CoverageTable.tsx` (+ tests + mocks). `ConstituentTable.tsx` /
   `api.ts` `/api/constituents` (constituent **prices**/weights) left intact — they are what we now
   capture. **Residual owner choice (non-blocking):** reconvert the dropped column into explicit
   constituent-**price** (daily-bar) coverage, or leave it dropped. Defaulted to dropped.

## Lanes this moots

ADR 0051 dissolves the option-capture throughput emergency. On landing, retire / re-scope:
`ibkr-capture-cross-underlying-concurrency`, `ibkr-snapshot-warmup-concurrency`,
`ibkr-intraday-conid-cache`, `EMERGENCY-capture-throughput`, `EMERGENCY-constituent-lane-activation`
— any residual value is only the index's *own* chain (already inside the window).

## Out of scope / deferred

Trading a *pure implied-correlation* dispersion (single-name option straddles) would re-open
constituent-option capture with its own capture-cost / IBKR-entitlement / throughput case — a new,
separately-ruled decision, **not** assumed here.
