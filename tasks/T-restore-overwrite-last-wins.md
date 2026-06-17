# T-restore-overwrite-last-wins — one run/day, idempotent re-capture (revert the run-partition casse)

**Status:** open — **P0** (2026-06-17). **Lane:** `infra-`/`ibkr-` (storage + capture) + `frontend-` (one BFF/web removal).
**Source:** the 2026-06-17 deep ingestion audit. **Blocks:** [T-clean-ingestion-2026-06-16](T-clean-ingestion-2026-06-16.md)
+ all recompute-from-raw ([platform-rebuild-nonraw-from-raw](archive/platform-rebuild-nonraw-from-raw.md)).
**Relates:** [infra-raw-invariant](infra-raw-invariant.md) (ADR 0040 raw-before-derived), ADR 0051.

## The casse (forensic-confirmed on the 2026-06-16 SX5E close)

A re-fetch of the same `trade_date` **accumulates** instead of overwriting → 42 capture runs banked
into one day. One option's `bid` carried **42 rows** (1 `session_id`, `canonical_ts=15:30` for all,
but 38 distinct `exchange_ts` 08:21→15:30 and 9 distinct values — i.e. 42 fires at different
wall-clocks, each recording the then-current quote, all stamped to the nominal close, **appended**).
`reconstruct_day` then builds 2+ snapshots at one PK → `DuplicateKeyInBatch`; the front shows 42
timestamps. **It worked before `b10ed3d`** because the derived layer *overwrote* (one run shown),
masking a latent raw-idempotency bug.

## Blueprint mandate (the target)

**One settled close per `(instrument, field)` per day, idempotent re-capture** — overwrite-last-wins on
the live slot (or an explicit `version=` lineage), never run-accumulation:
- `15-data-governance.md:9` raw = Tier-1, replayable; `:18` *"any replay/backfill must write a **new
  version identifier** instead of silently mutating/accumulating"* (versioned-replace, not 42 anon rows).
- `06-runbooks.md:32` *"raw partitions are **finalized**"* — one finalized partition per day.
- `b10ed3d`'s premise ("a re-fetch **can't overwrite** a prior run") directly contradicts this.

## Two roots — both restore to a known-good pre-casse git state

### C1 — Raw: make `07c011f`'s idempotency actually work (stable `event_id`)
`07c011f` ("fix(capture): **idempotent** append-only re-writes") added the raw dedup
(`eod_stages.py:236-241`: write only events whose `event_id ∉ existing_ids`). **Its intent is correct**
— a re-fire of the same observation should be a no-op. **The bug:** `event_id =
sha256(instrument_key, field_name, sequence)` (`market_fields.py`, `sequence` from origin `bc64973`),
and `sequence` is the **membership-sorted ordinal** within each run's list (`cp_rest_close_capture.py:343-346`).
When a re-run's live quotes shift which options pass the two-sided / quarantine filter, the same
instrument lands at a **different `sequence` → different `event_id` → not deduped → appended.**
- **Fix:** drop `sequence` from the content hash — key `event_id` on `(instrument_key, field_name)`
  (+ `trade_date`), so one row per instrument-field-day, **last write wins**. This makes the
  `07c011f` idempotency real regardless of membership drift. (`sequence=` at `cp_rest_close_capture.py:346,361`
  becomes inert; keep it as a column if useful, just out of the identity hash.)
- **Plus a run-state gate** so a 2nd close fire for an already-captured `(trade_date, underlying)`
  **replaces** rather than re-appends (none exists today in `scripts/eod_run.py`/`eod_stages.py`).

### C2 — Derived: retire the run-partitioning (pure revert to `b10ed3d^` = `c665614`)
`run_partitioned` **did not exist before `b10ed3d`** (`registry.py` pre-`b10ed3d` count = 0) — the
derived layer *overwrote*. Revert it:
- Remove `run_partitioned=True` from the 8 specs + the field default — `contracts/registry.py` (`:57,130,142,154,166,178,190,205,317`).
- Remove the `run=` segment + `ADHOC_RUN` — `storage/partitioning.py:74,91-93`.
- Remove `_filter_runs` + call site + `runs_for` + the dual-level globs — `storage/adapter.py:210,358-403,417-424,532,540-585`.
- Drop `run_id` threading — `adapter.py:53,80,99,107`, `eod_stages.py:280,288`, `actor/driver.py`, `signals/signal_set.py`, `qc_job.py`.
- Delete `scripts/migrate_run_partition.py`; delete `tests/test_run_partitioning.py`; revert the run= assertions in `test_live_spine_wiring.py`/`test_live_capture_spine.py`/`test_gateway_capture.py`.
- **Front:** remove the per-run selector — `apps/frontend/src/.../routers/recorded_dates.py:53-68` (`_RunLedger` per-run view); collapse `/api/recorded-dates` to **one entry per trade_date**. (Matthieu's per-fetch timestamp selector is superfluous — we don't have multiple runs/day.)

## C3 — Clean the corrupt 2026-06-16 slot (→ T-clean-ingestion-2026-06-16)
Before re-validating: dedup the raw to one observation per `(instrument, field)` keeping the latest
`exchange_ts` (the close), then re-derive. Recompute-from-raw then works.

## Acceptance
- A 2nd close fire for an already-captured day **replaces** (raw + derived) — verified by a re-fire test
  with quote drift producing **one** run, not two.
- `reconstruct_day` on a re-fired day no longer raises `DuplicateKeyInBatch`.
- The front day-selector shows **one entry per trade_date**.
- Full gate green; the run-partition machinery + tests removed; goldens regenerated as needed.

## ⚠️ Coordination
Touches shared storage (`adapter.py`/`registry.py`/`partitioning.py`), `eod_stages.py`, capture
(`cp_rest_close_capture.py`/`market_fields.py`), and the BFF — high-collision with the active fleet
(C1-C6/A6/D2). **One owner, serialized; stage by explicit path.** Pure-revert C2 first (lowest risk),
then C1 (event_id + gate), then C3 clean.
