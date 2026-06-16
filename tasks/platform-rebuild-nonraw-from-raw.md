# platform-rebuild-nonraw-from-raw — one command to purge derived + replay from the raw keystone

**Owner:** Matthieu · **Lane:** `platform-` · **Priority:** P2 (operational primitive)

> **Status (2026-06-17): LANDED (core scope).** `scripts/rebuild_from_raw.py` ships the guarded
> wrapper around `reconstruct_day` (tests: `packages/infra/tests/test_rebuild_from_raw.py`; full gate
> green). It asserts raw present before any purge, resolves the original close `as_of` from the
> snapshot layer (or `--as-of`), backs up then purges the snapshot/derived/projected-analytics
> partitions `reconstruct_day` owns, replays from raw, and hash-verifies `raw/` is untouched —
> reproducing the derived layer byte-for-byte and idempotent on re-run. **Deliberately out of scope
> (deferred):** re-running QC and reconstructing the `signals` layer. `reconstruct_day` does not
> produce QC results or strategy signals (QC needs `qc_inputs` from `run_analytics_with_qc`, the
> actor/EOD path), so those partitions are left untouched rather than purged-without-rebuild. Wiring
> a QC re-run is a clean follow-on over `run_qc` + the existing grid-point adapter — not new
> reconstruction logic, but beyond "wrap `reconstruct_day`".

## Why

Raw is the immutable keystone (ADR 0040); everything else (derived/analytics/qc/snapshot) is
reconstructable. When a schema evolves, old derived partitions go non-conforming (the 2026-06-15
incident: `projected_option_analytics/2026-06-12` lacked the now-required `surface_side` →
broke the signal layer → had to be hand-purged). There is no clean operator primitive for this —
today it is manual `rm -rf` + a re-capture. We should be able to **purge all non-raw for a date /
range and rebuild it from the stored raw**, deterministically, without re-hitting the broker.

## The building block already exists

`reconstruct_day` (`infra/orchestration/reconstruction/batch.py:80`) replays the stored raw and
rebuilds the derived layer (it is what `smoke_e2e` stage-1 "replay" uses). This task is the
**operator wrapper**, not new reconstruction logic.

## Scope

A guarded `scripts/rebuild_from_raw.py` (or a flag on an existing entry point):
- input: index + trade-date (or range);
- **never touches `raw/`** (assert raw present first; refuse if raw is missing for the day);
- purges the non-raw partitions for the day(s) — `analytics/`, `derived/`, `qc/`, `snapshot/` — and
  the day's run-state lines (back up first, like the manual incident did);
- calls `reconstruct_day` to rebuild from raw; re-runs QC;
- idempotent and safe to re-run (now that append-only writes dedup byte-identical —
  [[intraday-dryrun-pollutes-prod-slot]] fix).

## Acceptance

- `rebuild_from_raw --index SX5E --trade-date D` reproduces the derived layer byte-for-byte from
  raw (provenance stamps stable), QC re-runs, raw untouched (hash-verified).
- A schema-evolution drill: bump a derived contract, run rebuild, the old partition is replaced
  conforming — no hand-purge.

## Links

Pairs with `platform-data-durability` (backup/restore). Uses `reconstruct_day`.
