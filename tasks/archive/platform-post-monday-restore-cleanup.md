# T-post-monday-restore-cleanup — purge the Friday-restore ledger + posterior data after Monday's close

> **✅ DONE 2026-06-17.** Trigger met (real 06-15 + 06-16 closes banked). Removed the 5 synthetic
> `trade_date=2026-06-12` stage-rows (single synthetic run_id `32592ce2…`, ts `12:31:34`) from
> `data/_run_state.jsonl` (425→420 lines; backup `data/_run_state.jsonl.bak-pre-monday-cleanup`).
> Step 2 (posterior data) was a **no-op** — no `version=` reconstruction sub-partitions exist on
> disk for 06-12. Ledger now carries only real captured runs (06-15: 181, 06-16: 239).

> **Trigger:** AFTER Monday's (2026-06-15) real SX5E close-capture is captured **and validated**.
> Do **not** run before — it would delete the only front-visible state in the interim.

## Why

To make the front usable over the weekend (pending Monday's capture), the 2026-06-12 (Friday)
run-state ledger — which had been **purged** to free the idempotency slot — was **restored**
(`data/_run_state.jsonl` rebuilt so the front's recorded-dates view shows the Friday day off the
already-computed 2026-06-12 derived partitions). That restore is a **stopgap**: it re-introduces
exactly the kind of ledger/slot state the close-capture discipline clears before a real fire
(see the "intraday dry-run pollutes prod slot" / "clear ledger+partitions before the real close"
operating notes).

## Scope (run after Monday's snapshot is validated)

1. **Ledger.** Remove the Friday-restore entries from `data/_run_state.jsonl` (the synthetic
   stage-runs recorded for `trade_date=2026-06-12`), OR clear+rebuild the ledger from the real
   Monday run, so no stopgap bookkeeping survives next to live runs.
2. **Posterior data.** Audit and purge any derived/analytics partitions written *after* the
   Friday snapshot purely to serve the weekend front (any `version=<V>` reconstruction
   sub-partitions, any re-derived 2026-06-12 outputs that diverge from the validated Friday
   state). Keep the canonical validated Friday partitions if still wanted; drop the stopgap ones.
3. **Verify** the front then reflects the **Monday** run as the live state, with the ledger clean
   (one real run per fired close, no leftover synthetic rows).

## Done criteria

`data/_run_state.jsonl` carries only real captured runs; no stopgap/reconstruction artifacts
remain in `data/`; the front shows Monday's validated state; idempotency slot clean for the next
close. Then archive this task.
