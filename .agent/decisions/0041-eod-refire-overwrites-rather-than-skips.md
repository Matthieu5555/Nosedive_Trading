# 0041 — EOD re-fire overwrites rather than skips: the idempotency model, refined

- **Status:** accepted, 2026-06-10. Refines the restart/idempotency behaviour of
  [[0032-unattended-scheduling-via-systemd-timers]]. Lands in `orchestration/pipeline.py`.
- **Date:** 2026-06-10.
- **Implements:** blueprint **Part I** (determinism: "re-run is byte-identical or intentionally
  versioned") and **Part IV Step 15** (idempotent, restartable jobs) under
  [[0011-blueprint-as-plan-of-record]].
- **Relates to:** [[0032-unattended-scheduling-via-systemd-timers]] (the timer fires this
  one-shot), [[0035-index-registry-and-per-index-capture-schedule]] (the per-calendar @XEUR /
  @XNYS timers this unblocks), [[0040-ingestion-persistence-invariants]] (raw-before-derived /
  complete-or-flagged — overwrite is the mechanism that makes a partial day self-heal).

## Context

The EOD pipeline gated each stage on the run-state ledger: `completed_stages(trade_date)` →
`if stage in already_done: skip`. The ledger keys on **`(trade_date, stage)` only**
(`run_state.py`), with **no index/calendar dimension**. Two failures followed from this:

1. **The per-calendar timers cannibalise each other.** ADR 0035 schedules one timer per exchange
   calendar — `eod-capture@XEUR` (≈16:15 UTC, SX5E) and `eod-capture@XNYS` (≈20:45 UTC, SPX).
   The XEUR fire records `(2026-06-10, collection)` etc.; the later XNYS fire reads those as
   `already_done` and **skips collection/analytics → SPX is never captured**. Confirmed live:
   a temp-store run of `--index SX5E` then `--index SPX` skipped the second index entirely under
   the old gate.
2. **An intraday dry-run polls the slot shut.** Running the runner mid-session records the day's
   stages; the real close then skips them, so the operator had to *manually* clear the ledger +
   partitions before every real fire (the documented "intraday-dry-run pollutes the prod slot"
   footgun).

The skip existed to make a crash-restart "resume only the unfinished tail". But every stage write
is already idempotent — derived tables are **replace-by-`(trade_date, underlying)`**, the raw
layer is **append-dedup on the content-addressed `event_id`** — so re-running is safe by
construction, not just on the tail.

## Decision

**A re-fire RE-RUNS every stage rather than skipping the ones the ledger recorded.** The pipeline
runs `universe_refresh → collection → analytics → reconciliation → qc` unconditionally for its
fired index set; the ledger still **records** each stage (observability, missed-day catch-up,
gap-tracking) but is **no longer an execution gate**. Because the writes are idempotent, a re-run
of a given fired set converges to the same store state, and — the point — a *different* calendar's
fire writes its **own** index's partitions (per-underlying) without being blocked by, or
clobbering, the other's.

This is the minimal, non-colliding fix: it lives entirely in `orchestration/pipeline.py` and does
**not** touch the `(trade_date, stage)` ledger keying in `run_state.py` (which a concurrent
work-stream owns). A future per-`(trade_date, calendar)` ledger key (ADR 0040 / orchestration
follow-up) would let the skip return as an optimisation; until then, overwrite is correct and
cheap for a once/twice-a-day one-shot.

## Consequences

- **Both per-calendar timers capture their index.** Validated live: `--index SX5E` then
  `--index SPX` into one store leaves `raw_market_events`/snapshots/surfaces for **both**
  (SX5E preserved, SPX added; the second fire logged `skipped=[]`).
- **Intraday pollution self-heals.** The real close overwrites an intraday/dry-run's partitions —
  no manual ledger purge. (The one-off purge done on 2026-06-10 to recover the already-polluted
  slot is no longer a recurring chore.)
- **A crash-restart re-runs all stages** (not only the tail) — slightly more work, same converged
  state. Idempotent by construction; `systemd Restart=on-failure` retries stay safe.
- **Edge: a same-calendar restart re-snapshots that calendar's close.** Re-capture is absorbed by
  the raw layer's content-id dedup; a settled close is quote-stable, so a re-fire a minute later
  is byte-identical or a negligible last-tick update. (A different-calendar fire never re-captures
  another calendar's index — its fired set excludes it.)
- **Tests updated to the new contract** (`test_orchestration.py`, `test_eod_run.py`): a re-fire
  asserts `ran == all stages`, `skipped == ()`, and a byte-identical converged store — replacing
  the old "second fire skips every clean stage / is a no-op" assertions.
- This **supersedes the skip-on-restart behaviour** documented in ADR 0032 §restart; the timers,
  catch-up, and manifest freeze of 0032 are otherwise unchanged.
