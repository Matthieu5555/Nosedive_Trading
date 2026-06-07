# D1 — Storage foundation: `provider` partition segment (close the 0017 gap)

> **Foundational, pre-equity-scale.** [[0017-provider-dimension]] requires `provider` as a partition
> segment **before equity data is written at scale** — retrofitting after data is on disk is expensive
> (it rewrites the Parquet layout). This is the one storage change that must land before Phase 1C
> equity capture. The rest of the data architecture is fixed by ADRs 0015/0017/0019/0028/0033/**0034**.

- **Owns:** `packages/infra/src/algotrading/infra/storage/{partitioning,adapter}.py`, the persisted
  `provider` on the analytics contracts / registry it requires, and the storage tests. Conforms to
  **[ADR 0017](../.agent/decisions/0017-provider-dimension.md)** and
  **[ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md) §4**.
- **Depends on:** nothing structural — the query layer (0033) and provider plumbing above storage
  (`ProviderFlow`, per-provider `resolve_config`, `provider` on the collector event) already exist.
- **Blocks:** Phase 1C equity capture (writing SX5E/SP500 at scale). Crypto-only (DERIBIT, where
  `provider == exchange`) works without it, which is why it has not bitten yet.

## State going in (audited 2026-06-07)

0017 is **partially** implemented:
- ✅ `ProviderFlow` protocol + per-provider config resolution (`orchestration/provider_flow.py`).
- ✅ `provider` on the **collector** event (`collectors/normalize.py`, default `DERIBIT`).
- ❌ `provider` is **absent from the storage partition layout** (`partitioning.py` /
  `adapter.py` key is `trade_date × underlying [× version]` — no `provider`).
- ❌ `provider` is **not a column on the analytics contracts** (`contracts/`), so the partitioner has
  no per-record source to partition by.

So two sources writing the same `underlying` (e.g. ASML from SAXO and IBKR) would **mix on disk** and
`ReplaySource` would interleave them — corrupt surfaces, broken backtests (the exact failure 0017
exists to prevent).

## Objective

`provider` is a first-class partition segment for every table that carries source-specific data, so
two sources of the same symbol can never mix on disk and a scan/`ASOF JOIN` that omits `provider`
cannot accidentally cross sources. Physical layout (per ADR 0034 §4):

```
<root>/<layer>/<table>/provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]/data.parquet
```

`code_version`/`config_hash` stay in the `ProvenanceStamp` + manifest (NOT partition dirs); restatement
stays the `version=<V>` segment.

## What to do (ordered)

1. **Decide the per-record `provider` source.** Either (a) add a `provider` field to the persisted
   analytics contracts (frozen-contract change → additive-nullable, registry + serialization round-trip,
   the schema-evolution rule already supports it), or (b) derive it at persist time from the run's
   provider context threaded into `ParquetStore.write`. Prefer (a) for raw + the source-traceable
   derived tables (it is self-describing on the row, which lineage wants); use the additive-nullable
   path so old partitions stay readable.
2. **Add `provider` to `partitioning.py`:** derive it (record field → fallback rule), validate it
   (non-empty, single path segment), and put it **first** in `partition_dir`/`partition_file`
   (`provider=<P>/trade_date=…/underlying=…`).
3. **Thread `provider` through `ParquetStore`:** `write` (partition by it), `read`
   (optional `provider=` filter), `list_partitions`, `list_versions`, `delete_partition`,
   `source_records_for` (lineage resolves within the right provider).
4. **Registry:** mark which tables are provider-partitioned (raw + source-specific derived) vs
   provider-agnostic (e.g. portfolio/run metadata). Not every table needs it.
5. **Migration note:** existing on-disk data is crypto-only (DERIBIT); document that a one-time
   re-capture or a `provider=DERIBIT` backfill repartition is the migration (cheap — small/no equity
   data yet). Do it before any equity write.

## Test surface

Read [TESTING.md](TESTING.md). Specific:
- Two providers writing the same `(underlying, trade_date)` land in **disjoint** partitions and never
  mix; a `read` without a `provider` filter does not silently merge them where that would be wrong.
- A `read(..., provider=P)` returns only that source; lineage (`source_records_for`) resolves within
  the same provider.
- Round-trip: a record with `provider` serializes/deserializes; an old partition lacking the column
  still reads (additive-nullable).
- The four acceptance criteria of blueprint Step 4 still hold (efficient daily queries; replay==live
  schema; recompute one derived partition without rewriting raw).
- Gate green: `ruff && mypy && lint-imports && pytest`.

## Done criteria

`provider` is a partition segment for source-specific tables; no two providers of one symbol can mix
on disk; reads/lineage are provider-scoped; existing crypto data migrated or re-captured under
`provider=DERIBIT`; the storage layout matches ADR 0034 §4; root gate green.

## Not in this task (carried-forward / phase-gated)

- **Retention enforcement + cold-compaction-by-ticker** (ADR 0034 §2/§3): build **when storage
  pressure is measured**, not now. Tracked as a carried-forward item.
- **New tables** `DailyBar` (P0.3/1C), `IndexConstituent` (1A, bitemporal Parquet + `ASOF`),
  `ProjectedOptionAnalytics` (1F), gated `FuturesPoint` (1D): specified per phase as each opens.
