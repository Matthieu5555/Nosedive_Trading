# platform-rebuild-nonraw-from-raw — one command to purge derived + replay from the raw keystone

**Owner:** Matthieu · **Lane:** `platform-` · **Priority:** P2 (operational primitive)

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
