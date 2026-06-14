# infra-sx5e-weighted-membership — a weighted SX5E membership source + top-N-by-weight resolver

> **Source:** TARGET §0 (universe = one enabled index + its top-N by weight, point-in-time) + §3 S1
> + §7.4. **Found by the 2026-06-14 IBKR-coverage audit:** [[ibkr-constituent-option-capture]]
> (and therefore the flagship S1 dispersion book) rests on "point-in-time top-N by index weight",
> but neither the weighted SX5E source nor the top-N selector exists.

## The gap

Verified in `packages/infra/src/algotrading/infra/universe`:

- The only membership **sources** are `SP500DatasetsSource` (S&P 500), `YfiuaSnapshotSource`
  (Yahoo snapshot), and the generic `CsvFileSource`. **None deliver weighted SX5E membership** —
  and SX5E is the sole live index (TARGET §0). Yahoo is owner-excluded as unreliable (OQ-2).
- `members(as_of_date)` returns `BasketMember(constituent, weight)` with **nullable** weight, but
  there is **no top-N-by-weight selector** anywhere (no `nlargest`/sorted-by-weight resolver).

So "buy ATM straddles on the top-10 SX5E constituents by index weight" cannot resolve today: the
weights aren't ingested and nothing ranks them. `ibkr-constituent-option-capture` lists this inside
its scope but doesn't own it as a step; pulling it out makes the S1 precondition explicit.

## Scope (infra/universe, level below the broker leaf)

- A **weighted SX5E membership source** producing dated `MembershipChange` rows with non-null
  weights (SSGA / index-provider factsheet, or a vetted `CsvFileSource` feed) — point-in-time,
  append-only, ingested through the existing `ingest_membership_changes` (full-snapshot path, the
  weights-sum-to-1.0 guard already enforced).
- A pure **top-N-by-weight resolver**: `top_n_by_weight(index, as_of_date, n) -> tuple[...]` over
  `members()`, N from config (course top-10, theory top-50), deterministic tie-break, rejecting a
  basket with missing weights (can't rank what isn't known) with a labeled error.

## Depends on / blocks

- Builds only on the landed membership store/contracts; no broker dependency.
- **Blocks** [[ibkr-constituent-option-capture]] (which capture scope to widen to) and thus
  [[strategy-s1-dispersion]] and the implied-correlation [[infra-signal-layer]] (R3).

## Done criteria

A dated, weighted SX5E membership snapshot is ingestible and banked; `top_n_by_weight` returns the
point-in-time top-N by index weight deterministically, config-driven, rejecting incomplete-weight
baskets; expected values derived independently in tests, not copied from code; gate green.
