# daily-bar-compaction — fix the daily_bar small-files bloat (ADR 0034 cold-compaction)

> **READY — rulings recorded (owner, 2026-06-10); awaiting explicit go on implementation.** The
> threshold ADR 0034 names ("adding SP500, or file-count past a bound") is **crossed**. Options
> OQ-1…4 are ruled (see Rulings). No code until the owner says "go implémentation".

- **Owns:** `packages/infra/src/algotrading/infra/storage/adapter.py` (`_partition_files` date-range
  pruning at ~237-360, `read` at ~362); the daily_bar `TableSpec` in
  `packages/infra/src/algotrading/infra/contracts/registry.py`; a one-shot migration script
  `scripts/compact_daily_bar.py` (**new**); tests under `packages/infra/tests/`.
- **Reads (must stay byte-identical at the row level):** the front price-history candlestick
  (`apps/frontend/src/algotrading/frontend/routers/price_history.py:61`, the
  `store.read("daily_bar", underlying, start_date, end_date)` date-range path), the constituents
  date-range read (`routers/constituents.py:101`), the basket stock-leg spot lookup
  (`routers/basket.py:100`).
- **Depends on:** [ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md)
  (cold-compaction by ticker, behind the `StorageRepository` port, **cold data only**),
  [ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md) (DuckDB over
  Parquet — predicate pushdown on a `trade_date` column is the columnar equivalent of a date index).
- **Blocks:** nothing functional, but it de-risks scale (S&P500+) and the clean-slate before the
  SX5E re-capture; it also speeds every daily_bar date-range read.

## State going in (measured 2026-06-10)

`daily_bar` inherits the platform-wide partition convention `provider=IBKR / trade_date=YYYY-MM-DD
/ underlying=TICKER / data.parquet` (registry `primary_key=("provider","underlying","trade_date")`,
provider-partitioned). That convention is correct for the **per-day EOD capture cadence** (one
atomic, replace-semantics write per `(day, ticker)`), but daily_bar is the one table that is **bulk
immutable history** (1980→2026, queried by date range), so the convention degenerates into **one row
per file**:

- **419 755 files**, each **5 146 bytes**, each holding **1 OHLC row**.
- **4.9 GB** on disk for **~20 MB** of real data — **~250× overhead** (99.6 % is per-file Parquet
  footer/schema/metadata).
- Reads pay the cost too: a 2-year candlestick window opens ~500 file footers; the date-range path
  prunes by walking `trade_date` partition dirs (`adapter.py:275-360`).

`SX5E` (5 328 files) + `SPX` (5 598 files) + the full S&P500 constituent set make up the 419k.

## Objective

Store daily_bar cold history as **one Parquet file per ticker** with `trade_date` as a sorted
**column** (DuckDB predicate pushdown over row-group min/max stats = an implicit date index, ADR
0033), so 419 755 files → ~500, 4.9 GB → ~30 MB, and date-range reads prune by row-group instead of
by opening thousands of footers — **with the read API and every row byte-identical**.

## What to do (ordered) — pending the rulings below

1. **Migration script `scripts/compact_daily_bar.py`.** Read all `(provider, ticker)` rows across
   `trade_date` partitions via DuckDB, write one compacted file per the ruled layout (OQ-1), sorted
   by `trade_date`. **Verify row-identity** (count + content hash per ticker, old vs new) before any
   deletion. Old small files archived/deleted per OQ-3.
2. **Read path (`adapter.py`).** Teach `_partition_files` / `read` to resolve daily_bar from the
   compacted layout and push the `[start_date, end_date]` predicate onto the `trade_date` column
   (`read_parquet(...) WHERE trade_date BETWEEN ? AND ?`), falling back to / unioning the hot
   per-day partitions per the tiering ruling (OQ-2). The public `store.read(...)` signature is
   unchanged.
3. **Write path / tiering (OQ-2).** Decide how new daily captures land and when they roll into the
   cold compacted file (ADR 0034: compaction is cold-only — hot recent days stay per-day, cold
   history is compacted; the read unions both).
4. **Tests.** (a) Row-identity: compacted read == pre-compaction read for several tickers + windows
   (independently derived, not golden-only). (b) Date-range correctness: inclusive bounds, open
   bounds, empty window, unknown ticker → all match the current behaviour. (c) The front candlestick
   payload is byte-identical before/after. Run `check-lookahead-bias` on the as-of/date-range path.

## Rulings (owner, 2026-06-10)

- **OQ-1 — compacted layout → one file per ticker.** `provider=IBKR/underlying=TICKER/data.parquet`,
  rows sorted by `trade_date` (the column DuckDB pushes the date-range predicate onto). Revisit
  `year=YYYY` sub-partitioning only if a single ticker's file later grows past a bound.
- **OQ-2 — hot/cold tiering → (b), cold-only compaction.** New daily captures keep landing as per-day
  hot partitions (capture write stays atomic/idempotent, cadence unchanged); cold history is rolled
  into the per-ticker compacted file on a cadence. The read **unions hot + cold** and dedups on the
  `(provider, underlying, trade_date)` key. Honours ADR 0034 "compaction over cold data only".
- **OQ-3 — old-files disposition → archive then delete (safe).** After row-identity verification,
  move the superseded small files to `data/_archive/` and delete only after one green prod cycle
  proves the compacted read (data is gitignored/local, so the archive is a reversible safety net).
- **OQ-4 — scope → daily_bar only.** The sole acute case (bulk immutable history). The per-day EOD
  tables (raw_market_events, snapshots, surfaces, …) are correctly partitioned by the capture cadence
  and are left untouched.

## Done when

Root gate green; row-identity + date-range tests pin compacted == pre-compaction for sampled
tickers/windows; the front candlestick renders byte-identical; daily_bar file count down from
~419 755 to ~ (ticker count); `du -sh data/raw/daily_bar` down from 4.9 GB to tens of MB; the migration
is reversible until the old files are deleted; ADR 0034's compaction is marked landed (for daily_bar)
with the layout decision recorded.
