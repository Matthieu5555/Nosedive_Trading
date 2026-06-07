# 1A — Universe & membership: index → point-in-time constituents → per-name chain

- **Owns:** the new `IndexConstituent` bitemporal reference contract + its table registration
  (`packages/infra/src/algotrading/infra/contracts/tables.py` + `registry.py`), a membership
  ingester that loads dated add/remove changes into the Parquet store, and an as-of resolver
  `members(index, as_of_date)` on `packages/infra/src/algotrading/infra/universe/` that returns
  the historical basket with weights. Conforms to **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)**
  (blueprint is plan of record; the OQ-3 membership ruling is a *blueprint* ruling and overrides
  on domain), **[ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)**
  (DuckDB `ASOF JOIN` over Parquet for every point-in-time join), and **[ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md) §4**
  (storage layout / partition discipline, per D1).
- **Depends on:** P0 — the membership contract pin (the blueprint amendment for the OQ-3 ruling,
  tracked in `tasks/P0-contracts-and-unblockers.md`); D1 (`tasks/D1-storage-foundation.md`) for the
  storage layout this contract writes under. D1 names `IndexConstituent` explicitly as a 1A contract
  ("`IndexConstituent` (1A, bitemporal Parquet + `ASOF`)"). **[1J](1J-index-registry.md)** supplies the
  *set of indices* membership resolves for — the registry's `index` keys (`SX5E`, `SP500`/`SPX`) are
  the keys `members(index, as_of_date)` takes; this WS resolves what is *inside* each, 1J says *which*
  indices exist ([ADR 0035](../.agent/decisions/0035-index-registry-and-per-index-capture-schedule.md)).
- **Blocks:** 1C equity capture (capture needs the as-of basket to know which names to record on a
  given day) and 1I front page 1 (the scrollable constituent list is the resolved membership).
- **State going in (audited 2026-06-07):** `infra/universe/contracts.py` + `service.py` provide the
  **generic** `(instrument_key, as_of_date)` key and discovery/chain machinery; `UniverseService`
  accessors `symbols()` / `get_underlying()` / `get_option_chain()` / `resolve_contract()` are all
  generic over a single as-of date. `universe/README.md` documents membership as roadmap WS 1A
  building on that key ("Point-in-time membership (roadmap WS 1A) builds on this key"). What does
  **not** exist: any ingestion of dated index membership, any `IndexConstituent` table (`tables.py`
  stops at `TriageRecord`), and any as-of `members(...)` resolver. There is also **no** DuckDB
  `ASOF JOIN` helper anywhere in `storage/` yet — this WS introduces the first one.

## Objective

Index→constituent membership exists as point-in-time reference data: each constituent stored with
`(effective_add_date, effective_remove_date)` and an as-of weight, so a join for any past date
reconstructs the basket *as it stood that day* — never today's list applied backwards. A resolver
`members(index, as_of_date) -> basket with weights` answers that join through a DuckDB `ASOF JOIN`
over the Parquet store. Prove it on **EURO STOXX 50 (SX5E)** first; S&P 500 (~504 names, ~5y of
dated changes) is the stretch goal on the same contract and resolver, not a second code path.

This is the single most look-ahead-sensitive piece in Phase 1. Every historical membership join
resolves as of the date being reconstructed; `check-lookahead-bias` over the resolver and its
callers must pass with zero findings.

## What to do (ordered)

1. **Define the `IndexConstituent` contract** as bitemporal reference data, frozen like the other
   contracts in `tables.py`. Fields at minimum: `index` (the index symbol, e.g. `SX5E`),
   `constituent` (the member instrument key), `effective_add_date`, `effective_remove_date`
   (nullable / open-ended for a current member), the as-of `weight`, and the bitemporal *knowledge*
   axis (when this membership fact was recorded / which vendor snapshot it came from) so a later
   vendor restatement does not silently overwrite history. Register it in `registry.py` with its
   table name. **Decide provider-agnosticism:** membership is reference data, not per-broker market
   data — record the *vendor* (OQ-3 source: Siblis Research) as a field/knowledge axis, and decide
   per D1's registry whether this table is provider-partitioned or provider-agnostic. Default to
   provider-agnostic (it describes the index, not a quote source); justify in the contract docstring.
2. **Pick the storage shape.** Bitemporal Parquet under the ADR 0034 §4 layout (partition by
   `index`, then trade/effective date as D1 prescribes). Membership changes are sparse — store the
   *changes* (dated add/remove rows), not a daily-dense snapshot, and let the resolver expand to a
   basket. Keep the immutable-record discipline (0019/0034): a restatement is a new knowledge-axis
   row, never an in-place edit.
3. **Build the ingester.** Load dated membership changes (the OQ-3 source) into the contract and
   write through the storage adapter. Keep raw-source parsing separate from the typed contract so a
   second source (STOXX review history cross-check for SX5E; EODHD/CRSP for SP500) lands on the same
   contract. Validate on write (no negative weights; `effective_remove_date >= effective_add_date`;
   weights for a basket on a given date are sane — sum near 1.0 within tolerance where the source
   provides full weights, otherwise labeled as unavailable, never silently zeroed).
4. **Build the as-of resolver** `members(index, as_of_date) -> basket with weights` over the Parquet
   store using DuckDB's native **`ASOF JOIN`** (ADR 0033). The resolver returns exactly the
   constituents whose `[effective_add_date, effective_remove_date)` interval contains `as_of_date`,
   with that date's weights. This is the gate every historical membership join goes through — there
   is no path that reads "current" membership for a past date.
5. **Wire it to the universe service.** Expose the basket through `infra/universe/` so 1C capture and
   1I's constituent list consume one resolver, not their own joins. The existing generic
   `(instrument_key, as_of_date)` accessors stay; this adds the membership-to-basket layer above them.
6. **Run `check-lookahead-bias`** over the resolver and every call site as an explicit step, and
   leave a note in the resolver docstring stating the as-of contract so a future caller cannot
   accidentally pass "today" for a historical date.

## Test surface

Read `tasks/TESTING.md`. The expected values below are derived independently of the code under test
(real dated SX5E membership changes hand-encoded in the fixture, the basket for a date computed by
hand from those changes — never by calling the resolver and asserting it equals itself):

- **As-of basket correctness (the load-bearing case).** Fixture: a small set of dated SX5E changes
  spanning at least one add and one removal. For a date *before* a known addition, the resolver's
  basket **excludes** that name; for a date *after* the addition and *before* its removal it
  **includes** it; for a date *after* removal it **excludes** it again. The included set for each
  probe date equals the hand-computed set in the test comment. This is the test that proves no
  look-ahead.
- **No-lookahead boundary.** On the exact `effective_add_date` the name is in (or out — pin the
  half-open `[add, remove)` convention and test the boundary day explicitly, both ends). A name
  removed on date D is absent for D under the chosen convention; assert the boundary, do not leave
  it implicit.
- **Today's-list-is-not-history guard.** Construct a fixture where the *current* basket differs from
  a *past* basket; assert `members(index, past_date)` returns the past basket and never the current
  one. A direct negative assertion that the resolver does not fall back to the latest membership.
- **Weights as-of.** A constituent whose weight changed between two dates resolves to the correct
  weight for each date; basket weights for a date sum within tolerance where the source is complete
  (float comparison with an explicit tolerance, not `==`).
- **Bitemporal restatement.** A later vendor snapshot that restates a past membership writes a new
  knowledge-axis row; resolving "as the data was known on the earlier snapshot date" still returns
  the original basket (the bitemporal property — corrected history does not erase what was known).
- **Contract round-trip / seam (A-side discipline, TESTING.md "Seam tests").** An `IndexConstituent`
  writes and reads back equal through the storage adapter and validates against its registered
  schema; at least one **malformed** instance (negative weight; `remove < add`; empty `index`) is
  **rejected** by write-ahead validation with an explicit error, not silently coerced.
- **Edge cases (TESTING.md floor).** Empty membership for an unknown index (labeled empty basket,
  not a crash); a single-constituent index; an as-of date before the index's earliest record
  (empty, labeled); an open-ended (never-removed) current member resolved for today.
- **Determinism / reordering invariance (TESTING.md).** Ingesting the same dated changes in a
  shuffled order produces the same on-disk membership and the same resolved baskets; resolver output
  is order-independent (sorted basket).
- **`check-lookahead-bias` passes** over the resolver module and its callers with zero findings —
  this is a named, required gate, not advisory.

## Done criteria

`IndexConstituent` is a frozen, registered bitemporal contract; the ingester loads the OQ-3-sourced
dated SX5E changes into the Parquet store under the D1 / ADR 0034 §4 layout; `members(SX5E,
as_of_date)` returns the correct historical basket with as-of weights via a DuckDB `ASOF JOIN`; the
as-of basket test and the today's-list-is-not-history guard are green; `check-lookahead-bias` passes
with zero findings; the universe service exposes the basket for 1C/1I; SP500 demonstrated on the
same contract and resolver as the stretch goal; root gate green (`uv run` — `ruff && mypy &&
lint-imports && pytest`). uv only; no other runner.

## Gotchas

- **The whole point is the as-of join.** It is trivial to ship a resolver that reads the latest
  membership and "works" on a recent date — that silently reintroduces look-ahead on every past
  date. The negative guard (today's basket ≠ past basket, asserted directly) is what proves the
  build, not the happy-path basket test.
- **Half-open intervals, pinned once.** Decide `[effective_add_date, effective_remove_date)` and test
  both boundary days. An off-by-one on the boundary is a look-ahead bug, not a cosmetic one.
- **Bitemporal vs unitemporal.** Vendors restate index history; if you store only the effective axis
  you cannot answer "what did we believe on date X" and a restatement rewrites the past in place
  (violates 0019/0034 immutability). Keep the knowledge axis.
- **Provider dimension (D1).** Membership is reference data describing the index, not a per-broker
  quote stream — do not blindly inherit the `provider=` partition segment D1 adds for market data.
  Decide explicitly and justify; record the *vendor* as a field, which is a different concept from
  the storage `provider` segment.
- **One resolver, not per-consumer joins.** 1C and 1I must both call `members(...)`; if either writes
  its own membership join the look-ahead audit has two surfaces to police instead of one.
- **Weights may be partial.** Where the source does not give full weights, label them unavailable —
  never default to zero or equal-weight silently (a silent default is a TESTING.md negative-path
  failure and an economic-correctness bug).
- **Independent oracle.** The expected baskets come from real dated changes hand-encoded in the
  fixture and computed by hand in the test comment, per TESTING.md — never assert the resolver
  against its own output.
