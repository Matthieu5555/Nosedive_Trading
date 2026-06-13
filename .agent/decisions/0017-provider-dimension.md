# 0017 — Provider dimension: `provider` as a first-class field and partition key

> **AMENDED 2026-06-13 (T-index-only-refactor).** The multi-broker scenario that motivates this
> ADR (Saxo + IBKR capturing the same symbol) is historical — Saxo/Deribit were removed, IBKR is
> the sole live broker. The `provider` dimension itself **stays** (generic, load-bearing, kept for
> a possible future broker); only the motivating example is dated. See ADR 0023's amendment.

- **Status:** accepted
- **Date:** 2026-06-03
- **Source:** Vincent's ADR-019; merged 2026-06-05

## Context

With multiple brokers (Deribit, Saxo, IBKR) potentially capturing the same underlying symbol from
different sources, the absence of a `provider` field in `RawMarketEvent` creates a silent collision:
two sources writing to the same `underlying` partition would mix their events, and `ReplaySource`
would replay them interleaved with no way to filter by source. For crypto (Deribit only), `provider`
and `exchange` coincide — which masked the distinction. For equity, **provider ≠ exchange**: the
same ASML option can arrive from Saxo or IBKR on the same Euronext listing. Confusing them produces
corrupt surfaces and broken backtests.

A secondary issue: each broker has its own config bundle (forward bounds for crypto differ from
equity bounds). A runner loading `infra/configs/qc.yaml` equity bounds for a Deribit crypto surface
caused forward QC rejections and empty surfaces.

## Decision

1. **Add a `provider` field to `RawMarketEvent`** (and as a partition segment and schema column in
   all derived stores). `provider` = the data source leaf (`DERIBIT`, `SAXO`, `IBKR`).
   `exchange` = the market listing venue (`DERIBIT`, `AMS`, `NASDAQ`). They are distinct fields;
   do not conflate them.

2. **Partition key includes `provider`.** `(provider, underlying, trade_date, code_version,
   config_hash)` — a SQL query or scan that omits `provider` cannot accidentally join two sources
   of the same symbol.

3. **`ReplaySource` filters by `provider`.** A replay of IBKR data never includes Saxo ticks.

4. **Config resolution is per-provider.** Each broker leaf (`infra-<broker>/configs/`) holds its
   own forward-bounds and universe config. The runner calls `resolve_config(provider)` which loads
   the correct bundle; the generic `infra/configs/qc.yaml` is never used for broker-specific bounds.

5. **`ProviderFlow` Protocol in `infra`** (beside `MarketDataAdapter`): `open_session()`,
   `discover(...)`, `make_adapter(...)`, `resolve_config(...)`. Each leaf implements it. A registry
   in the app/frontend layer maps `provider` → `ProviderFlow` instance; `infra/` never imports a
   leaf.

6. **The `provider` dimension must be added before equity data is written at scale.** It is a
   partition write-path change — retrofitting after data is on disk is expensive. This is why it
   was prioritized over the DuckDB query layer (ADR 0015 §4), which is purely additive on reads.

## Consequences

Replay, surfaces, and risk outputs are source-traceable by construction. Cross-provider joins in
DuckDB require an explicit `provider` predicate — accidental source mixing is prevented at the
schema level. Exit cost: medium (changes the partition layout of existing Parquet data; new data
written with the field, old data migrated or re-captured).
