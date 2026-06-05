# infra.orchestration.reconstruction

Historical replay/backfill over a date range. Reconstruction is **not** a second compute
path — it is `actor.run_analytics` run over each stored day, the *identical* code as
live (ADR 0007 d4). This subpackage adds only the batch layer on top of that one
function.

## Fast path

```python
from algotrading.infra.orchestration.reconstruction import (
    reconstruct_day, reconstruct_range, compare_replay_to_live,
)

report = reconstruct_range(
    store, start, end, positions,
    instruments=instruments, masters=masters,
    config=config, config_hash=cfg_hash,
    as_of_for=as_of_for, calc_ts_for=calc_ts_for,   # injected per-day timestamps
    version="v2",                                    # optional restatement version
)
report.reconstructed_dates    # days that produced ≥1 derived record
report.missing_dates          # days with no stored raw partition — flagged, never filled
```

## What it guarantees

- **A missing raw partition is flagged, never interpolated.** A day with no stored raw
  partition is reported `MISSING` with `outputs=None` — distinct from `EMPTY` (partition
  present, no usable quotes). No fabricated empty result fills a gap.
- **Versioned restatement.** `version="v2"` writes each derived table under its own
  `version=` sub-partition, leaving the prior analytic intact beside it; `version=None`
  is byte-for-byte the actor's own replace-in-place persist (one live implementation).
- **Replay == live under one code version.** `compare_replay_to_live` reads the live
  rows back per table and compares by primary key then full value, naming the exact
  divergent table and keys if they ever drift — it measures agreement, never assumes it.

## Tests

`packages/infra/tests/test_replay_reconstruction.py` — the named robustness cases:
missing-flagged-not-masked, empty≠missing, a multi-day range end to end (a compressed
month, stated out loud), versioned restatement with the old version surviving, and
replay-vs-live agreement plus its divergence-naming counter-case.
