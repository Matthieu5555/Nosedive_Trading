# T-restore-overwrite-last-wins — one validated close per day, overwrite-last-wins (revert the run-partition casse)

**Status:** open — **P0** (2026-06-17). **Lane:** `infra-`/`ibkr-` (storage + capture) + `frontend-` (one BFF/web removal).
**Source:** the 2026-06-17 deep ingestion audit. **Owner-validated logic (2026-06-17).**
**Blocks:** [T-clean-ingestion-2026-06-16](T-clean-ingestion-2026-06-16.md) + all recompute-from-raw
([platform-rebuild-nonraw-from-raw](archive/platform-rebuild-nonraw-from-raw.md)).
**Relates:** [infra-raw-invariant](infra-raw-invariant.md) (ADR 0040), [EMERGENCY-quote-integrity-gate](archive/EMERGENCY-quote-integrity-gate.md), ADR 0051.

---

## 1. The casse (forensic-confirmed on the 2026-06-16 SX5E close)

A re-fetch of the same `trade_date` **accumulates** instead of overwriting → **42 capture runs banked
into one day.** Evidence: one option's `bid` carries **42 rows** — 1 `session_id`, `canonical_ts=15:30`
for all, but **38 distinct `exchange_ts`** (08:21→15:30) and **9 distinct values**. I.e. 42 close polls
fired at different wall-clocks, each recorded the then-current quote, all stamped to the nominal close,
**appended**. `reconstruct_day` then builds 2+ snapshots at one PK → `DuplicateKeyInBatch`; the front
shows 42 timestamps. **It worked before `b10ed3d`** because the derived layer *overwrote* (one run
shown) and a **run-state ledger gate** blocked re-fires of a finalized day — both removed by the casse.

Two independent breakages combine:
- **`b10ed3d`** added derived **run-partitioning** (`run_partitioned` flag + `run=` segment) → 42 `run=`
  partitions instead of overwrite. (`run_partitioned` did not exist before `b10ed3d`; count = 0.)
- The **raw idempotency is defeated by an unstable `event_id`** + the **ledger gate is gone** → re-fires
  append into the single raw slot.

---

## 2. The decided logic (owner-validated, blueprint-reconciled)

**The CP-REST close capture is a POLL** (`cp_rest_close_capture.py:300` `snapshot_with_warmup` — one
poll per fire), not a stream. So: **one validated close observation per `(instrument, field)` per day.**

**A re-fetch OVERWRITES the day's slot with the most recent — last-wins — never versioned, never
accumulated.** This is the pre-mess design ("overwrite-by-re-run: replace tables + ledger gate"; the
`data/_run_state.jsonl` ledger still on disk proves a gate existed).

**🔒 OVERWRITE IS CONDITIONAL ON A NON-EMPTY CAPTURE (the critical safety).** A capture replaces the
canonical slot only when it carries real data — **owner-ruled 2026-06-17, the boundary is ZERO VALID
QUOTE, not "fewer than banked":**

> overwrite **iff** the new fire carries **≥1 valid two-sided quote** (`basket.two_sided_count > 0`,
> the collector's authoritative count — `cp_rest_close_capture.py`, predicate `is_valid_two_sided`).
> A re-fire with **zero** valid two-sided quotes (genuinely **empty / market-closed / last-only**) is
> **REJECTED at admission** when a slice is already banked for that `(trade_date, underlying)` — the
> prior good close stays untouched.

**flag-not-reject — do NOT amputate thin-but-real slices.** A basket with **few but real** two-sided
quotes (`count > 0`) **PASSES and overwrites**, and is **flagged downstream** (the front clamps a
degenerate ultra-short slice — `infra-surface-fit-quality` lane 2). The gate must **never** drop a
thin-but-real capture; the only rejection is the genuinely-empty re-fire. (We deliberately do **not**
compare completeness `≥ banked` — that would amputate a legitimately thinner-but-real close.) A **first
faithful land** (no prior banked) is always admitted so raw stays Tier-1 faithful and the degenerate
detector can page (ADR-0040 fail-loud only-if-zero-options-first-run).

**The admission gate** (`eod_stages.default_stages_builder`) reads the banked slice
(`store.read(raw_market_events, trade_date, underlying)`); a re-fire is admitted unless it is
zero-valid-over-a-banked-slice, and then `_collection` **replaces** (`delete_partition` + `write`,
last-valid-wins). This restores the lost overwrite gate that prevented the 42 accumulation.

### Blueprint backing (the reconciliation — why overwrite-last-wins IS conform)

Overwrite-last-wins **finalizes** the day to the latest *valid* poll — it replaces intraday **scratch**,
it does not silently mutate a *finalized* observation. The blueprint's "no silent overwrite / write a
new version" protects the **finalized** close, which here is enforced by the **gate**, not by routine
versioning:
- `06-runbooks.md:32` — *"raw partitions are **finalized**"* (one finalized partition per day) ✓ overwrite-to-finalize.
- `01-architecture.md:17` — a re-run is *"byte-for-byte identical or intentionally versioned"*: an
  identical re-poll is a no-op (idempotent); a deliberate post-finalization re-derivation is the **only**
  thing that uses `version=`.
- `01-architecture.md:13` / `15-data-governance.md:18` — "no silent overwrite of an upstream
  observation" / "write a new version identifier" govern the **finalized** layer + deliberate replay,
  **not** the within-day finalization of a poll.
- `15-data-governance.md:9` raw = Tier-1 replayable. `b10ed3d`'s anonymous `run=` is **neither**
  byte-identical **nor** versioned → it violates `01-arch:17`; we remove it.

**`version=` stays ONLY as the deliberate-replay escape hatch** (re-derive with new code/config; the
adapter already supports `version=`/`list_versions`). It is **never** on the routine close-capture path.
**Owner ruling: we overwrite with the most recent; we do not version the close.**

---

## 3. The fix

### C2 — Derived: retire run-partitioning (pure revert toward `b10ed3d^` = `c665614`)
Restores overwrite-last-wins on the derived layer (lowest risk; do first).
- Remove `run_partitioned=True` from the 8 specs + the field default — `contracts/registry.py`.
- Remove the `run=` segment + `ADHOC_RUN` — `storage/partitioning.py`.
- Remove `_filter_runs` + its call site + `runs_for` + the dual-level `run=`-aware globs — `storage/adapter.py`.
- Drop `run_id` threading — `storage/adapter.py`, `orchestration/eod_stages.py`, `actor/driver.py`, `signals/signal_set.py`, `orchestration/qc_job.py`.
- Delete `scripts/migrate_run_partition.py` and `packages/infra/tests/test_run_partitioning.py`; revert the `run=` assertions in `test_live_spine_wiring.py` / `test_live_capture_spine.py` / `test_gateway_capture.py`.
- **Front:** remove the per-run selector — `apps/frontend/src/.../routers/recorded_dates.py` (`_RunLedger` per-run view); collapse `/api/recorded-dates` to **one entry per `trade_date`** (newest validated close). Update the web header + tests.

### C1 — Raw: stable `event_id` + overwrite-on-revalidate + restore the gate
- **Stabilise `event_id`** on `(instrument_key, field_name, trade_date)` — drop the membership-ordinal
  `sequence` from the content hash (`market_fields.py`; `sequence=` at `cp_rest_close_capture.py:346,361`
  becomes inert, keep the column if useful). A re-poll of the same (instrument, field) is then ONE row.
- **Overwrite-last-wins on the raw close slot** on a re-fetch that clears the admission gate: replace the
  `(trade_date, underlying)` raw rows (`delete_partition` + `write`) rather than the prior append-only
  `event_id` set-difference (first-wins) — `eod_stages._collection`.
- **Admission gate** (`eod_stages.default_stages_builder`): thread `two_sided_count` onto `IndexBasket`
  (set by `collect_live_basket`); reject a re-fire iff `two_sided_count == 0` **AND** a slice is already
  banked for that `(trade_date, underlying)` (§2). Non-empty (incl. thin-but-real) and first-faithful-land
  always admit. Loud `rejected_empty_overwrite` log; the QC degenerate seam still pages if nothing banked.

### C3 — Clean the corrupt 2026-06-16 slot (executes [T-clean-ingestion-2026-06-16](T-clean-ingestion-2026-06-16.md))
The raw is not fire-tagged, so collapse to one observation per `(instrument, field)` keeping the **latest
`exchange_ts`** (= the close mark), then re-derive. The provisional archive
(`data/_provisional_archive/2026-06-16-pre-cleanup/`) is the rollback net. Recompute-from-raw then works.

---

## 4. Acceptance

- A 2nd close fire for an already-captured day **replaces** (raw + derived) **iff** it carries ≥1 valid
  two-sided quote — verified by `test_overwrite_last_wins.py`: (a) a valid re-fire replaces → **one** slot,
  latest values win; (b) a **zero-valid / last-only** fire is **rejected**, the prior close intact;
  (c) a **thin-but-real** fire (count ≥ 1) **PASSES and overwrites** — never dropped (flag-not-reject).
- `reconstruct_day` on a re-fired day no longer raises `DuplicateKeyInBatch`.
- The front day-selector shows **one entry per `trade_date`**.
- `version=` is absent from the close-capture routine path (replay-only).
- Full gate green; run-partition machinery + tests removed; goldens regenerated as needed.

---

## 5. ⚠️ Coordination & order

Touches shared storage (`adapter.py`/`registry.py`/`partitioning.py`), `eod_stages.py`, capture
(`cp_rest_close_capture.py`/`market_fields.py`), and the BFF — **high-collision** with the active fleet
(the 3-onglets + Stream-C/D work landed 2026-06-17). **ONE owner, in an isolated worktree, serialized;
stage by explicit path.** Order: **C2 (pure revert) → C1 (event_id + overwrite + gate) → C3 (clean)**,
gate green between steps. **Re-verify the current code state first** — the fleet moved storage/capture
this morning; line numbers in this spec are indicative, anchor on the symbols.
