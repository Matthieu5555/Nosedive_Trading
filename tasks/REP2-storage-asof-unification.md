# REP2 — Unify the storage as-of seam on duckdb/polars

> **READY — high care (look-ahead boundary).**
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md))
> The single most look-ahead-sensitive function is hand-looped while `membership.py`
> already does the *same shape* as a native engine query. Inconsistent + a scaling risk.

- **Owns:** `packages/infra/src/algotrading/infra/snapshots/as_of.py`;
  `packages/infra/src/algotrading/infra/storage/adapter.py` (lineage resolution + append dedup).
- **Depends on:** nothing. Landing this gives **polars its first real use** and closes the
  polars phantom-dep question in [REP0](REP0-dependency-hygiene.md).
- **Blocks:** nothing, but it de-risks every future point-in-time alignment (1A/1C/1E/1H all
  rely on the as-of discipline).
- **State going in:** `membership.py:238-313` resolves point-in-time membership with a clean
  DuckDB `ASOF JOIN` + `QUALIFY row_number()` — the model. `snapshots/as_of.py:27-54` does a
  per-field as-of by **hand** (Python loop, manual `event_id` tiebreak). The two as-of
  mechanisms are inconsistent. Conforms to
  [ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md).

## Objective

One consistent, engine-native as-of seam, so the look-ahead boundary is expressed once, the
way the rest of the storage layer already does it — and so the multi-year raw layer reads
don't full-scan.

## What to do (ordered)

1. **`snapshots/as_of.py:27-54` per-field as-of → engine query.** Replace the loop with a
   DuckDB `QUALIFY row_number() OVER (PARTITION BY field_name ORDER BY canonical_ts DESC,
   event_id DESC)=1` (the `membership.py:245-252` idiom) **or** the polars
   `filter(canonical_ts<=ts).group_by(field_name).agg(...)` equivalent. **Preserve exactly**
   the inclusive `<=` boundary and the `event_id` tiebreak (`_supersedes`).
2. **Property-test before/after:** shuffle-invariance and exact-tie behaviour must match the
   current impl. Run the `check-lookahead-bias` skill after. This is the boundary — no
   silent change to which value is "latest as of t".
3. **`adapter.py:356-391` lineage resolution → predicate pushdown.** Replace the
   full-table `read()` + Python list-comprehension filter with a DuckDB
   `read_parquet(...) WHERE (pk…) IN (…)` — **keep the full composite-key match**, not a
   single field. Pure read; real scaling win at year-scale raw data.
4. **`adapter.py:153-163` append dedup → Arrow `is_in` / DuckDB anti-join** instead of the
   `zip` + Python-set membership. Behaviour identical: reject a changed payload under an
   existing key (append-only immutability).
5. **Defer A3** (`pyarrow.dataset` for partition discovery, `adapter.py:198-238`) — note it,
   don't bundle. Large blast radius: the live/version partition separation is
   correctness-critical and partition columns are also stored inside the files.

## Done when

Root gate green; `check-lookahead-bias` clean on the snapshot path; property tests pin
as-of equivalence (shuffle + tie); lineage and dedup paths covered by tests asserting
identical results to the previous impl. If polars is the chosen engine for step 1, REP0's
polars line is resolved to "adopted".
