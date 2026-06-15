# platform-data-durability — a backup/restore story for the canonical data store

The week's whole thesis rests on "several days of harvested, QC-clean history" (TARGET §2.2),
and that history lives in `data/` as parquet that is **untracked and not git-recoverable**. A
disk loss, a bad purge, or a fat-fingered `rm` during an ops chore would destroy the one asset
the unattended week produces and cannot easily re-create (the close snapshots are point-in-time —
you cannot re-capture last Tuesday's close). There is no backup task on the board. This is that
task: a deliberate durability decision, not a silent omission.

- **Owns:** a backup/restore mechanism for the canonical store and the run-state ledger —
  `data/` (the partitioned raw + derived parquet) and `data/_run_state.jsonl`. A
  `scripts/`-level backup helper (or a documented systemd timer/rsync/object-store push), plus
  the restore-and-verify path. Touches **no** `packages/**` compute.
- **Depends on:** nothing structural. It must respect the capture discipline — **never** smoke-test
  or stage against the canonical store; validate restores into a *temp* store and diff.
- **Relates to:** [platform-post-monday-restore-cleanup](platform-post-monday-restore-cleanup.md)
  (the ledger purge) — that task *removes* stopgap state; this one ensures the *real* state can
  survive loss. The two must agree on what "canonical" means so a backup never re-seeds purged
  stopgap rows.
- **State going in (audited 2026-06-14):** `data/` is gitignored and not under any backup; the
  only durability-adjacent task on the board is a *purge*. The byte-identical replay substrate
  (immutable raw, as-of discipline) means a backed-up raw store can deterministically re-derive
  everything downstream — so the **raw store + ledger are the minimum that must survive**; derived
  partitions are reconstructable from them.

## What to do (ordered)

1. **Define the durability target.** Decide and record what must survive and to what RPO: at
   minimum the immutable **raw** partitions + `_run_state.jsonl` (everything derived replays from
   raw deterministically). Write it down — this is the decision the rest follows from.
2. **A backup mechanism.** A scheduled, append-only/immutable-friendly copy of the raw store +
   ledger to a second location (another disk, an rsync target, or an object store). Daily after the
   close fire is the natural cadence — chain it off the same per-index close the babysitter already
   knows, or a separate timer. No secrets in git if a remote target needs credentials (they live in
   `$HOME`/`.env`).
3. **A restore + verify path.** A documented command that restores a backup into a **temp** store
   and verifies it — partition counts, a checksum/manifest match, and a byte-identical re-derive of
   one day against the live derived output. Restore-into-canonical is an explicit, gated operator
   step, never the default.
4. **Document the runbook** alongside the deployment stack
   ([platform-deploy-stack-ownership](platform-deploy-stack-ownership.md)) — what is backed up, where,
   how to restore, how to verify.

## Test surface

- Backup of a temp store, then restore into a *second* temp store, byte-identical diffs clean.
- A simulated loss (delete a temp copy, restore from backup) recovers the raw store and the ledger;
  derived partitions re-derive identically.
- The backup path writes **nothing** into the canonical `data/` and carries no secret in git.
- Any helper is ruff/mypy clean (root gate stays green).

## Done criteria

The raw store + run-state ledger are backed up on a defined cadence to a second location; a
documented, tested command restores and verifies into a temp store; the RPO decision is on the
record; nothing in the path mutates the canonical store or commits a secret.

## Landed (2026-06-15)

`scripts/backup_data_store.py` — backup / restore / verify for the canonical store, with the
logic as importable functions (gate-tested) and a thin CLI:

- **`backup_store`** snapshots the **keystone** — the immutable `raw/` partitions +
  `_run_state.jsonl` — into `$ALGOTRADING_BACKUP_ROOT/<YYYYmmddTHHMMSSZ>/` as an **append-only**
  directory (`store/` mirror + `manifest.json` of per-file sha256). `--include-derived` adds the
  reconstructable `derived/`/`analytics/`/`qc/` trees; the default is raw+ledger because
  everything derived replays from raw (the byte-identical replay substrate). Reads canonical
  **read-only**; a colliding snapshot id is refused, never overwritten.
- **`verify_snapshot`** re-hashes a stored snapshot against its manifest (bit-rot detection).
- **`restore_store`** copies a snapshot into a target and re-hashes **every file vs the manifest**
  (the byte-identical guarantee). Refuses the canonical store unless `--allow-canonical`, and a
  non-empty target unless `--force` — so the default restore lands in a temp store you diff.
- **Destination is an explicit decision, not a default:** `--backup-root` / `$ALGOTRADING_BACKUP_ROOT`
  is required. This box has **one physical disk** (`nvme0n1p3`; no second disk / NAS mounted), so
  a silent same-disk default would masquerade as off-box backup. The tool fails loudly with none
  set, and the runbook says to point it at an external disk / NFS / object-store for disk-loss
  protection (same-disk = purge/fat-finger protection only).

**Cadence (RPO):** `scripts/systemd/data-backup.{service,timer}` fire daily at 19:30 Europe/Berlin
(after the 18:15 close capture) — worst-case loss is one trading day's close. `data-backup-alert.service`
is the `OnFailure=` target (a silent backup failure is the worst case). Units match the
`eod-capture` style; install is per-user alongside them.

**Docs:** `scripts/README.md` carries a "Backup & restore runbook" (what/where/RPO/restore-verify
+ the purge-coordination note) and a tools-table row.

**Tests** (`packages/infra/tests/test_backup_data_store.py`, 10 cases): backup→restore
byte-identical, simulated-loss recovery, source-untouched, canonical-restore refusal,
non-empty-target refusal, derived include/exclude, manifest corruption detection, append-only,
no-keystone refusal, destination resolution. Expected checksums derived independently with
`hashlib`. The whole suite runs in temp stores (never the canonical `data/`); the CLI was also
smoke-tested end to end (`backup`→`list`→`verify`→`restore`) against a temp store.

**Gate green:** ruff ✓, mypy (240 files) ✓, import-linter ✓, pytest 2085 passed / 12 skipped.

**Scope note / deferred:** the heavier "re-derive one day and diff against live derived" check is
left to the existing read-only replay (`scripts/plot_live_surface.py` / the smoke test's
byte-identical-replay invariant) — this tool proves the *raw* is faithfully recoverable, which is
the keystone guarantee; derived reconstruction is already covered elsewhere. Two **operator
actions remain**: (1) set `$ALGOTRADING_BACKUP_ROOT` to a real second location and install the
timer; (2) sequence the first real backup after [[platform-post-monday-restore-cleanup]] so it
captures the validated, post-purge state.

## Gotchas

- **Never validate against the canonical store** — restores and diffs go into a temp store; the
  live `data/` parquet is the thing being protected, not a scratchpad.
- **Raw is the keystone, not derived.** Backing up derived partitions is optional convenience;
  losing raw is the unrecoverable event, because derived replays from raw but raw replays from
  nothing.
- **Coordinate with the purge task.** A backup taken *before*
  [platform-post-monday-restore-cleanup](platform-post-monday-restore-cleanup.md) runs would preserve
  the Friday-restore stopgap rows — back up the *validated* state, or the purge and the backup will
  fight.
