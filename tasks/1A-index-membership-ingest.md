# WS 1A — Index membership ingest (point-in-time constituents)

> Roadmap workstream **1A** (`documentation/roadmap-index-analytics.md`). The blueprint
> (ADR 0011) overrides on any domain question. Opened 2026-06-07 to unblock the Tab-1
> constituent list (the front's left panel is empty because `index_constituents` has no rows).

## Why

Tab 1 resolves the basket via `members(index, as_of)` over the `IndexConstituent` contract
(`index_constituents`, `reference` layer). That table is **empty**, so the constituent list is
empty and no ticker is selectable — the whole price-first flow (candlestick + surface + Greeks)
is unreachable.

Index membership is **point-in-time reference data** (who was in the index, with dated
add/remove), not a market-data quote. It is **not available from IBKR** — verified against the
official IBKR docs, not assumed:

- **TWS API "Contracts"** (official): the API only matches a contract you specify; there is no
  enumeration of an index's members.
- **TWS API "Market Scanners"** (official): scan codes rank a top-N by metric
  (`HOT_BY_VOLUME`, `TOP_PERC_GAIN`, …); there is **no index-membership universe filter**.
- **IBKR API Solutions** (official answer): *"getting all securities in a specified index and
  their conids… not available via the API"* (same for ETF holdings).
- **IBKR's own Quant blog** sources S&P 500 constituents from **third parties** (Wikipedia /
  GitHub datasets / iShares holdings) — it would not if the API served them.

So membership must be sourced externally and we keep the dated history ourselves; IBKR is then
queried **per resolved name** for chains/bars (1B/1C). This is exactly the OQ-3 ruling
(point-in-time mandatory; source Siblis for the robust path) — with a **free** source for now.

**Source decision.** Yahoo / `yfinance` is **rejected**: (a) owner/prof mandate excludes Yahoo
as unreliable (OQ-2), and (b) `yfinance` has no constituents function — Yahoo's components page
is partial and current-only (no dated history). Use **`yfiua/index-constituents`** (free,
current **and** monthly-historical, Yahoo-consistent symbols) as the default; keep **iShares
ETF holdings** as a current-only cross-check and **Siblis Research** as the paid robust upgrade
(OQ-3) behind the same ingest seam.

## Contract (already defined — do not change)

`IndexConstituent` (`packages/infra/src/algotrading/infra/contracts/tables.py`): bitemporal —
`index, constituent, effective_add_date, effective_remove_date|None, knowledge_date, vendor,
weight|None`. Stored append-only in the `reference` layer, partitioned by index then
`effective_add_date`; resolved by the DuckDB ASOF JOIN in `universe.members` (ADR 0033). PK
`(index, constituent, effective_add_date, knowledge_date)` — a restatement is a new row.

## What to do (ordered)

1. **A source adapter, behind a seam.** Add a `MembershipSource` protocol returning, for an
   `(index, as_of_window)`, raw `(constituent, effective_add_date, effective_remove_date|None,
   weight|None)` facts + a `vendor` tag + a `knowledge_date`. First impl: `YfiuaMembershipSource`
   reading the committed CSV pattern `…/index-constituents/$YYYY/$MM/constituents-$CODE.csv`
   (download → parse). Keep it offline-testable from a committed sample fixture (no network in
   tests). iShares/Siblis are later impls of the same protocol.
2. **Map to `IndexConstituent`, bitemporal.** Build the half-open effective intervals from the
   dated monthly snapshots (a name present month M but absent M+1 → `effective_remove_date` =
   first absent month's start). Stamp every row with the run's `knowledge_date` and `vendor`.
   `weight` is `None` where the source omits it (never zero/equal-weight — OQ-1/OQ-3).
3. **Diff + append-only ingest.** On each run, resolve what we already store, diff against the
   pulled facts, and **append new rows on any change** (new `knowledge_date`) — never edit or
   delete an existing row (ADR 0019/0034 immutability). Re-running an unchanged day is a no-op
   (idempotent). Write through `ParquetStore` to the `index_constituents` spec.
4. **No look-ahead.** The ingest stamps `knowledge_date`; the resolver's `known_as_of` axis is
   what gates "what we believed on date K". Never apply today's list to a past `as_of`. Gate the
   historical join with `check-lookahead-bias`.
5. **Wire into the registry/cron, not ad hoc.** The puller is a scheduled job (alongside 1J/1C);
   adding an index is a registry edit (`configs/universe.yaml`, ADR 0035), not a code change. The
   resolved membership then feeds the per-name IBKR capture universe (1B/1C) — the "déversement"
   into the IBKR requests.
6. **Provenance.** Stamp the ingest run (source URL/file, vendor, fetch timestamp, code version)
   like every other write, so each membership row traces to its origin.

## Acceptance

- A run populates `index_constituents` for SPX (and SX5E if the source covers it); `/api/constituents?index=SPX&as_of=<date>` returns the historical basket, price-first, **200** with rows.
- The Tab-1 left list renders the constituents and a ticker becomes selectable end to end.
- Bitemporal correctness: a restatement appends a new `knowledge_date` row; no in-place edit.
- `members(index, as_of)` returns the correct point-in-time basket; `check-lookahead-bias` passes.
- Re-run is idempotent (no duplicate rows for an unchanged source).

## Test surface

Read [TESTING.md](TESTING.md). Independent-oracle, offline (committed sample CSV), float tols.
- `test_yfiua_csv_parses_to_membership_facts` — a committed sample CSV → expected `(constituent, add, remove, weight)` facts (hand-derived oracle).
- `test_monthly_snapshots_build_half_open_intervals` — a name present M, absent M+1 → `effective_remove_date` = M+1 start; still-present → `None`.
- `test_ingest_is_append_only_on_restatement` — a changed pull appends a new `knowledge_date` row; the prior row is untouched.
- `test_ingest_is_idempotent` — re-running an unchanged source writes no new rows.
- `test_members_resolves_point_in_time` — `members(SPX, past_date)` ≠ today's basket; `check-lookahead-bias` clean.
- `test_constituents_endpoint_reads_back` — BFF `/api/constituents` returns the seeded basket, price-first, 200.

## Depends on / notes

- Reuses: `IndexConstituent` contract, `universe.members` resolver (ADR 0033), `ParquetStore`,
  provenance stamping — all already built. This WS is the **ingest**, not the resolver.
- Feeds: 1B (delta-band chain selection) / 1C (per-name capture) take the resolved universe.
- Front: no change needed — Tab 1 already calls `/api/constituents`; it lights up once rows land.
