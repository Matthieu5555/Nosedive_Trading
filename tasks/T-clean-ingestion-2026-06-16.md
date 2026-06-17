# T-clean-ingestion-2026-06-16 — purge the constituent-option junk + full recompute-from-raw (06-15 & 06-16), re-capture 06-17 live

**Status:** open — **P1 data cleanup, BLOCKED on a prerequisite bug** (see §0). **Lane:** `platform-` (data ops).
**Owner ruling (2026-06-17, Vincent):** *"clean the raw of everything that isn't Stoxx50 options or daily bars, EMPTY all the computed layer, recompute from the cleaned raw, and re-capture today's live end-to-end."*
**Depends on:** the recompute-from-raw bug in §0 being fixed FIRST. **Relates:** [ADR 0051](../.agent/decisions/0051-return-to-blueprint-dispersion-realized-vol-diagnostic.md) (index options only; constituent option capture retired), blueprint Part XV (raw Tier-1, all derived recomputable from raw), [T-restore-overwrite-last-wins](T-restore-overwrite-last-wins.md) (the C1/C2/C1.2 overwrite fix — **landed** `5a5abf4`).

---

## 0. ⛔ BLOCKER — recompute-from-raw is INCOMPLETE (fix before any recompute)

A live verification on 2026-06-17 proved `rebuild_from_raw` / `reconstruct_day` does **not** reproduce
the front's data from raw — recompute would silently **blank the vol nappe**:

- Original 2026-06-16 had **117,216** `projected_option_analytics` rows (SX5E = 20,283). A
  `rebuild_from_raw` recompute produced **0** projected rows + **0** `pricing_results` (snapshot / iv /
  surface rebuilt fine).
- **Root cause:** `_build_projected_analytics` (`actor/driver.py:606`) early-returns `()` when
  `provider is None`. The live eod passes `provider="IBKR"` (`eod_stages._analytics`); but
  **`reconstruct_day` has no `provider` param at all** (`reconstruction/batch.py:52`) → `run_analytics`
  gets `provider=None` → zero projected + zero pricing on **every** recompute.
- **Also missing from reconstruct:** `strategy_signals` (ρ̄ / dispersion) and `qc_results` /
  `triage_records` are **not produced by `reconstruct_day` at all** (only the 9 `REBUILT_TABLES`).

This violates blueprint Part XV ("all derived recomputed from raw"). **Fix required before recompute
— OWNER RULING (2026-06-17): Option 1, full recompute (analytics + signals + qc):**

1. Thread `provider` (default `DEFAULT_PROVIDER = "IBKR"`) through `reconstruct_day` → `run_analytics`
   so `projected_option_analytics` + `pricing_results` regenerate. Verify `_persist_outputs` writes them.
   (Mirror the live eod `_analytics`: also `session_open=False`.)
2. **Extend `reconstruct_day` to also run `persist_signal_set` (ρ̄ / dispersion) + `run_qc`** for the
   day — so recompute-from-raw is blueprint-true Part-XV complete (analytics + signals + qc all
   regenerated). It already holds `config`; thread the signal config + qc thresholds.
3. Regression test: recompute a known day from raw → `projected_option_analytics` row count matches the
   live run (non-zero, within reconstruct's collapse semantics) + signals + qc present. **Own ADR/bug task.**

---

## 1. Approach (owner-ruled, blueprint-conform)

**One clean rule for the raw:** keep only **(a) SX5E option-chain events** and **(b) constituent
`daily_bar` prices** (the realized-vol input for Eq. 23 / ρ̄). Everything else in the close-capture
lane is ADR-0044/0045 junk (constituent option chains, pre-0051) → **purge**.

1. **Clean the RAW** of all non-{SX5E-options, daily_bar} partitions for the junk days.
2. **EMPTY the entire computed layer** (snapshot + derived + analytics + signals + qc) for those days —
   no surgical per-partition keep; wipe and recompute.
3. **Recompute** from the cleaned raw (needs §0 fixed).
4. **Re-capture 2026-06-17 live end-to-end** (the cron re-fetches at close anyway) — doubles as the
   live ingestion test of the landed overwrite-last-wins fix.

## 2. Scope — which days, which junk (mapped 2026-06-17, nothing mutated yet)

`raw_market_events` by `trade_date`:
- **06-12**: `SX5E` only → already clean, no action.
- **06-15**: 9 underlyings (ALV ASML ENR MC SAP SIE SU TTE + SX5E) → **8 constituent junk** → clean.
- **06-16**: **49** underlyings (SX5E + 48 constituents) → the big junk day → clean.
- **06-17**: `SX5E` only (ADR-0051-correct scope) → raw clean, but **2-fire accumulation** (captured
  pre-C1.1) → re-capture live e2e supersedes it.

**Junk footprint to purge (verified on a temp copy — full-clean validated):**
- `raw/raw_market_events` — 48 (06-16) / 8 (06-15) constituent `underlying=` partitions.
- `raw/instrument_master` — constituent option masters for the junk days.
- `reference/discovery_conid_cache` — constituent conid caches for the junk days.
- `qc/constituent_capture_outcomes` — **entire table is ADR-0051-RETIRED** → delete it wholesale (all dates).
- All computed: `snapshot/market_state_snapshots`, `derived/*` (forward_curve, iv_points, surface_*,
  pricing_results, risk_aggregates, scenario_results), `analytics/projected_option_analytics`,
  `signals/strategy_signals` (**provider-nested**: `provider=IBKR/trade_date=…`), `qc/qc_results`,
  `qc/triage_records` — wipe each `trade_date=DAY` dir (incl. all 42 `run=` + constituent subdirs).
- **KEEP / untouched:** `raw/daily_bar` (constituent **prices** = realized-vol input, separate backfill lane).

## 3. Validated facts (temp sandbox `/tmp/c3_fix`, zero canonical touch)

- Purge of all the above → 06-16 ends with **0 `run=`, 0 non-SX5E underlying** (excl. daily_bar), 0 retired table.
- `rebuild_from_raw --index SX5E`: snapshot 132,669 → **2,369 rows, 0 dups, SX5E only**; `reconstruct_day`
  ran clean (**no `DuplicateKeyInBatch`** — the prior failure was pre-C2, now resolved), raw hash-verified.
- BUT `projected_option_analytics` + `pricing_results` came back **0** → the §0 blocker.

## 4. Safety / order — **stage-in-temp, promote-after-validation (owner-ruled 2026-06-17)**

**No in-place `rm -rf` on canonical.** Build the cleaned+recomputed days in a **temp staging copy**,
validate fully, then **promote** (atomic swap) — the previous canonical kept as a deletable archive.
This makes the whole op reversible up to the final owner sign-off; no annoying irreversible loss.

- **Order:** ① fix §0 bug (+ regression test) → ② stage a temp copy of the junk days (06-15, 06-16)
  → ③ in the temp copy: purge junk (raw → SX5E-options + daily_bars only) + **wipe all computed** →
  ④ recompute in temp (analytics + signals + qc, via the §0-fixed reconstruct) → ⑤ **validate the temp
  result** (non-zero projected nappe, ρ̄, QC, zero junk) → ⑥ **promote** temp → canonical, moving the old
  canonical day-dirs into a `data/_provisional_archive/<day>-pre-cleanup/` → ⑦ re-capture 06-17 live
  e2e → ⑧ owner validates the front (nappe + ρ̄ + QC) → ⑨ **delete the provisional archives +
  `_rebuild_backups`** (no shadow data).
- A 210M archive for 06-16 already exists from an earlier pass; the promote step supersedes/refreshes it.

## 5. Acceptance

- 06-15 & 06-16 raw = **SX5E options only** (+ `daily_bar` untouched); zero constituent option junk anywhere.
- Computed layer fully recomputed from raw, **including non-zero `projected_option_analytics`** (front
  nappe reads), ρ̄ signals, and a current-code QC verdict; zero `run=` partitions; retired
  `constituent_capture_outcomes` table gone.
- 06-17 re-captured live end-to-end, one clean slot (overwrite-last-wins, no accumulation).
- Provisional archives + rebuild backups deleted after owner sign-off.
