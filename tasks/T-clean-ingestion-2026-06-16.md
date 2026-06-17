# T-clean-ingestion-2026-06-16 — re-derive (post-0052 QC) + prune the 2026-06-16 SX5E close

**Status:** open — **P2 operational housekeeping** (2026-06-17). **Lane:** `platform-` (data ops).
**Source:** the 2026-06-17 ingestion audit (yesterday's close) + [T-intent-vs-delivery-audit-findings-2026-06-16](T-intent-vs-delivery-audit-findings-2026-06-16.md).
**Tool:** `scripts/rebuild_from_raw.py` (landed `ced031a`).

## Why

The banked **2026-06-16** SX5E close shows `qc=fail` — but that verdict is **PRE-0052** (run 15:05
CEST, before the QC recalibration `f1a6205`/[ADR 0052](../.agent/decisions/0052-qc-coverage-floors-to-blueprint-interpolate-and-fallback.md)
landed at 23:59). The current code would not reproduce it. There are also **42 stale `run=` partitions**
(overwrite churn) for the day. **Raw is intact and complete** (verified — incl. the last-only LEAP
events), so **everything here is recompute-from-raw; nothing is lost.**

## Scope (blueprint: Part XV — raw is Tier-1, all derived recomputable from raw)

0. **Provisional archive (safety net — do this FIRST, before any mutation).** Snapshot **everything
   that will be altered** to `data/_provisional_archive/2026-06-16-pre-cleanup/` (out of the canonical
   read path): the 2026-06-16 derived partitions (`surface_grid`, `qc_results`, `option_quote_snapshot`,
   `projected_option_analytics`) **and** the 42 stale `run=` partitions about to be pruned. (`rebuild_from_raw`
   already backs up its own purge targets to `_rebuild_backups`, but this archive is **explicit and
   complete** — it also covers the pruned runs, which the script's backup does not.) Recoverable until the
   post-validation cleanup below.
1. **Re-derive** the derived layer for `trade_date=2026-06-16` from raw via
   `scripts/rebuild_from_raw.py` (no broker re-hit; raw byte-identical, hash-verified). One clean run.
2. **Re-run QC** for 2026-06-16 under current (post-0052) code → a clean verdict replacing the
   pre-0052 CRITICAL. ⚠️ `rebuild_from_raw`/`reconstruct_day` does **not** produce `qc_results` (out of
   scope there) — run QC separately via the `run_qc` path. Sequence: re-derive → re-run QC.
3. **Prune the 42 stale `run=` partitions** (surface_grid / qc_results / snapshot / analytics) for the
   day, keeping only the re-derived clean run. Pure duplicates — housekeeping.

## NOT in scope (blueprint-conform — do NOT do these)

- **Do NOT remove the degenerate ultra-short slice** (~2-3d). Blueprint is **flag-not-reject**
  (`04-implementation-guides:208`): the slice **stays, flagged**; the FRONT clamps it (that is
  [infra-surface-fit-quality](infra-surface-fit-quality.md) lane 2, re-homing onto the Onglet-1 nappe).
  This task does not touch surface content.
- **Do NOT delete the last-only 2y/3y raw events** — valid `last` events, kept as the audit trail
  (they correctly never reached `iv_points`/`surface_grid`).

## Caution — no precipitation

Everything is recompute-from-raw → reversible, nothing lost. **`rebuild_from_raw --dry-run` first**
(reports targets, touches nothing). Validate the re-derived run + the new QC verdict **before** pruning.
The only genuinely unobtainable data is two-sided 2y/3y LEAP quotes the broker never made.

## Acceptance

- 2026-06-16 has **one** clean run with a **post-0052 QC verdict** (the pre-0052 CRITICAL gone).
- The 42 stale `run=` partitions pruned; raw byte-identical (rebuild's hash-verify passes).
- The degenerate ultra-short slice is still present-and-flagged (untouched); its front clamp is
  tracked separately under `infra-surface-fit-quality` lane 2.

## ⚠️ Post-validation cleanup (REQUIRED follow-up — do NOT skip)

The provisional archive (Step 0) is a **rollback net only**. Once the re-derived run + the post-0052
QC verdict are **validated by the owner** (the front nappe reads clean, the QC verdict is sane), the
archive must be **deleted** so it does not become stale shadow data:

- [ ] Owner validates the re-derived 2026-06-16 run + QC verdict.
- [ ] **Delete `data/_provisional_archive/2026-06-16-pre-cleanup/`** and the `rebuild_from_raw`
      `_rebuild_backups` entry for the day.
- [ ] Confirm the canonical read path holds exactly **one** clean run; close this task.

Until this runs, the provisional archive is the recovery source — **do not delete it before owner sign-off.**
