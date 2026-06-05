# reconstruction

Historical reconstruction and replay over a date range. Reconstruction is the actor's
`run_day` run over a span of stored trade dates — the *same* compute path as live, not
a second engine — plus the batch layer the roadmap (step 13) asks for on top: walk a
range in order, flag days with no stored raw data, optionally restate into versioned
partitions so a newer-code run never overwrites an older analytic, and compare a
reconstruction to the previously-persisted live outputs to catch drift.

## TL;DR

Point it at a store that already has raw events on disk, give it a date range and the
day's instruments/masters/positions, and it replays each stored day and returns a
report of what reconstructed, what was missing, and why. Nothing is interpolated
across a gap: a day with no raw partition is a named `MISSING` outcome with no output,
distinct from an `EMPTY` day (raw present, nothing derived).

## Fastest path: backfill a date range

```python
from datetime import UTC, date, datetime
from orchestration.reconstruction import reconstruct_range

def as_of_for(d):   return datetime(d.year, d.month, d.day, 15, 30, tzinfo=UTC)
def calc_ts_for(d): return datetime(d.year, d.month, d.day, 16, 0, tzinfo=UTC)

report = reconstruct_range(
    store, date(2026, 3, 2), date(2026, 3, 31), positions,
    instruments=instruments, masters=masters,
    config=config, config_hash=config_hash,
    as_of_for=as_of_for, calc_ts_for=calc_ts_for,   # injected per-day clocks
)

print(report.reconstructed_dates)   # days that produced derived rows
print(report.missing_dates)         # days with no stored raw partition
```

`as_of_for`/`calc_ts_for` map each trade date to that day's snapshot and computation
instants. They are injected, never read from a wall clock, which is what makes a replay
reproduce the original bytes — the same property the byte-identical headline test
relies on. To reconstruct a single day, use `reconstruct_day(...)` with one `as_of` and
one `calc_ts`. `stored_trade_dates(store)` lists the dates that actually have a raw
partition, i.e. the days a reconstruction *could* replay.

## How missing data is reported

Every day in the range comes back as one `DayReconstruction` with a `status`:

- `RECONSTRUCTED` — the raw partition existed and the actor produced at least one
  derived record. `outputs` holds the `ActorOutputs`.
- `MISSING` — no raw partition is stored for that day. `outputs` is `None`. There is
  deliberately no fabricated empty result, so you cannot mistake "absent" for "present
  but empty". `report.missing_dates` lists exactly these days.
- `EMPTY` — the raw partition existed but yielded no derived rows (e.g. only one-sided
  quotes, nothing usable). A real, distinct fact from `MISSING`.

`report.day(d)` returns the outcome for one date and raises `KeyError` if you ask about
a date that was never in the range — a date you never requested is a loud error, not a
silent "missing".

## How versioned partitions work

By default reconstruction writes the unversioned, replace-in-place layout — the live
path — so re-running a day overwrites that day's derived files exactly as live does.
Pass `version="<V>"` to restate into a coexisting `version=<V>/` sub-partition instead.
A restatement under a new version lands *beside* any existing analytic rather than over
it, so newer code never silently destroys the older numbers (the versioning is A's
storage feature; see `src/storage/README.md`).

```python
# Restate March under a new analytics version, leaving the live layer untouched.
reconstruct_range(store, date(2026, 3, 2), date(2026, 3, 31), positions,
                  instruments=instruments, masters=masters,
                  config=config, config_hash=new_hash,
                  as_of_for=as_of_for, calc_ts_for=calc_ts_for,
                  version="2026.06-recalib")
```

A version string is one path segment (no `/` or `=`). Use a stable, meaningful label —
a release tag or recalibration id — because it is how an operator reads that specific
restatement back later.

## How to read a specific restatement back

The version you wrote under is the key you read back with:

```python
store.list_versions("iv_points", trade_date, "AAPL")   # e.g. ['2026.06-recalib', ...]
store.read("iv_points", trade_date=trade_date, underlying="AAPL",
           version="2026.06-recalib")                  # exactly that restatement
```

`read(..., version=None)` returns the live (unversioned) rows only — a restatement
written `version=<V>` beside the live partition is *not* returned by a default read,
so the two never mix. Pass the explicit version to read one restatement; the live
partition and every restatement are read back separately.

## Replay vs live: catching drift

`compare_replay_to_live(store, trade_date, reconstruction.outputs)` compares a
reconstruction's outputs against the live rows already on disk for that day, per
derived table, by primary key and full value. Under the same code version they must
agree on every table — that is the determinism guarantee — so the helper exists to
flag a *future* divergence, naming the table and the exact keys that differ rather than
a bare "they differ". The read is scoped to the trade date and spans every underlying,
because the derived tables partition under different underlyings for one day (option
and surface tables under the real symbol, the portfolio-level risk aggregate under a
synthetic `_all`); a per-underlying scope would silently drop the risk rows.

## Tests

`tests/test_replay_reconstruction.py` pins the named cases: a missing partition is
flagged explicitly and never masked; restatements write to versioned partitions and the
old version survives alongside the new and reads back its own values; a multi-day range
(a compressed stand-in for "a historical month", said so out loud) reconstructs end to
end; and replay equals live on overlapping dates under one code version, with the
companion case that the comparison actually names a divergence when the versions differ.
