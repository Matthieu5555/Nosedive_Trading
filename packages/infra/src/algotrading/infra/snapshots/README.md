# snapshots — raw events to a quality-labeled market state

TL;DR: turn one instrument's raw market events into a `MarketStateSnapshot` as of a
point in time, with a labeled reference price and honest quality flags. This is the
"normalized market state" layer — clean up the mess, pick a sensible price, line
everything up by time — and it is the look-ahead boundary of the whole platform.

```python
from algotrading.infra.snapshots import build_snapshot, SnapshotContext

ctx = SnapshotContext(snapshot_ts=ts, qc=config.qc_threshold, calc_ts=ts, config_hash=h)
snapshot = build_snapshot(instrument, raw_events, context=ctx)
```

## The as-of read (the look-ahead boundary)

`latest_by_field_before(events, snapshot_ts)` returns the latest event of each
field at or before the instant. The boundary is **inclusive**: an event stamped
exactly at `snapshot_ts` is used; an event even a microsecond later is never used.
It compares on `canonical_ts` (A's designated ordering timestamp) and breaks
exact-timestamp ties by `event_id`, so the result does not depend on the order
events arrived — feeding the same events shuffled yields the same snapshot. This is
what makes replay deterministic and backtests honest.

## The reference price ladder

`resolve_reference_spot` picks the price by a fixed, labeled ladder, and the chosen
rung is recorded in `reference_type` — there is no hidden fallback:

1. `mid` — a valid two-sided quote (both sides present, positive, `bid <= ask`).
2. `last` — no valid two-sided quote, but a positive last trade.
3. `close` — a supplied prior close.
4. `carry_forward` — the last known spot, carried forward.

A crossed quote (`bid > ask`) is never turned into a mid; it falls through to the
next rung and is flagged by QC. When no rung applies, the instrument has no honest
spot and `build_snapshot` raises `InsufficientSnapshotData` rather than inventing a
zero; `build_snapshots` collects those as labeled skips so the gap is queryable.

The `close` and `carry_forward` inputs **must be point-in-time** (known at or before
`snapshot_ts`). The pure functions cannot verify that, so the caller owns the as-of
guarantee — a future close fed here would reintroduce look-ahead bias.

## Flags and completeness

Every snapshot carries flags that are set, never implied: `open`/`closed` (session
state), `stale_underlying`/`stale_option` (the latest quote is older than
`max_quote_age_seconds`; the boundary is exclusive, so exactly-at is fresh),
`fallback_spot` (the reference came from a non-mid rung). `completeness` is the
fraction of the three quote fields present. An option also inherits
`stale_underlying` when its underlying's own quote is stale (set by
`build_snapshots`, which builds underlyings first).

## Quote QC (step 7)

`assess_quote` runs the named single-quote checks — crossed/locked, bid positivity,
spread width, quote age, open interest, price-vs-intrinsic — and reduces them to one
`QuoteAssessment` with the worst severity (`usable`/`caution`/`reject`) and every
reason code, so a rejection is fully auditable. Cross-sectional checks that need the
whole chain live elsewhere: `cross_strike_monotonicity_violations` here, and the MAD
outlier rejection with the forward engine (Eq 24).

QC is **wired into the build path**, not an optional afterthought a consumer may
forget. `assess_snapshot` returns the snapshot paired with its verdict, and
`build_snapshots` runs the assessment on every built snapshot and returns a
`SnapshotBatch` that keeps **both** views:

- `batch.snapshots` — the full set, every snapshot regardless of verdict;
- `batch.usable` — the QC-filtered subset (verdict not `reject`) that downstream
  forward/IV code should consume;
- `batch.assessed` — each snapshot with its `QuoteAssessment`, so a rejected quote
  stays queryable with its reasons instead of being silently dropped.

The verdict is assessed from the **raw observed** `bid`/`ask` (`None` when a side is
absent), not the snapshot's projected fields — those store `0.0` for a missing side,
which would otherwise read as a spurious locked or non-positive quote. Assessing it
beside the staleness decision keeps the verdict consistent with the snapshot's own
`stale_*` flags by construction (both keyed off `max_quote_age_seconds`).

A's `MarketStateSnapshot` carries no QC field, so the verdict rides alongside the
snapshot in the batch rather than on the contract — the same split the forward engine
uses between its rich in-memory `ForwardEstimate` and the flat persisted
`ForwardCurvePoint`. Persisting QC verdicts as queryable rows is the operations QC
plane's job (`QcResult`, Workstream E), fed from this assessed batch.

## Worked example

A two-sided quote of `bid = 99.8`, `ask = 100.2` at or before `snapshot_ts`
resolves to `reference_type = "mid"`, `reference_spot = 100.0`, and
`spread_pct = (100.2 - 99.8) / 100.0 = 0.004`. Drop the ask and the same instant
falls to the next rung: a positive `last = 99.5` gives `reference_type = "last"`,
`reference_spot = 99.5`, `is_fallback = True`, and a `fallback_spot` flag. A crossed
quote (`bid = 100.2 > ask = 99.8`) never becomes a mid — it falls through the ladder
and QC marks it `reject` with reason `crossed`. With all three quote fields present,
`completeness = 1.0`; with only bid and last, `completeness = 2/3`.

## Determinism, provenance, and the C-layer boundary

Pure functions, no I/O and no wall clock — `calc_ts` is injected. Every snapshot
carries a provenance stamp naming the exact raw events that fed it, by full
`(session_id, event_id)` key. The as-of read is order-independent, so feeding the
same events shuffled yields a byte-identical snapshot, which is what makes replay
deterministic. This is the framework-free layer: the actor (Workstream E) hands it
raw events and a `SnapshotContext` and persists what comes back; it never reaches
into the price-selection or QC logic.
