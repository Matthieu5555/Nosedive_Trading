# 1D — Futures term structure (gated): listed-futures capture as secondary

> **GO — owner ruling, 2026-06-17: pursue listed-futures capture opportunistically.**
> The option-implied forward and the listed future carry the **same** information, so the
> derived forward (`ForwardCurvePoint`, already built) stays **primary and sufficient** for
> analytics and nothing on the critical path waits on this. But the owner ruled that **where
> listed-futures data is obtainable from IBKR, we should capture it** — the teacher's Tab-1
> (Données) brief puts a futures term-structure grid in the data tab. This **supersedes the
> ADR-0037 forward-only deferral for the capture decision.**
>
> **The blueprint amendment is now a step to execute, not a gate that keeps this parked.** The
> blueprint stays the amendable domain contract ([ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md));
> futures are **not** in it yet, so the first task is still **not code** — land a **futures
> blueprint amendment + a `FuturesPoint` contract ADR** (which exchanges/contracts, roll
> convention, settlement, day count, listed-contract→pinned-tenor mapping), then build. Until both
> exist, write them — do not wait on them. The reconciliation discipline below stands unchanged:
> futures are **captured/secondary**, the derived forward is **never displaced**; 1D runs
> **parallel** to 1A→1I, never on the critical path.

- **Owns:** *(only once gated open)* a futures-points contract under
  `packages/infra/src/algotrading/infra/contracts/tables.py` (extend `ForwardCurvePoint` or add a
  new `FuturesPoint` — `tasks/D1-storage-foundation.md` already names `FuturesPoint` as the gated
  1D contract), its registry entry, a capture path for listed-futures quotes, and the
  forward-vs-futures cross-check. Conforms to the new ADR P0.4 lands and to
  **[ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)** /
  **[ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md) §4**
  for storage.
- **Depends on:** the **futures blueprint amendment + `FuturesPoint` ADR** authored in task 1
  (the owner ruling 2026-06-17 supplies the GO; these artifacts are produced here, not awaited). Then:
  `tasks/D1-storage-foundation.md` (the `provider` partition segment must already be a first-class
  segment so captured futures cannot mix sources), and the pinned tenor grid from **P0.1**.
- **Blocks:** **nothing on the critical path.** It is a cross-check on a value that is already
  derived independently. 1A→1I ship with forward-only and are complete without it.
- **State going in:** **NOTHING exists.** No futures contract, no futures capture, no futures in
  the blueprint or data dictionary. The option-implied forward already exists and is primary;
  this WS adds the *captured* secondary leg and the reconciliation between the two.

## Objective

Capture the **listed-futures term structure** for the index on the pinned tenor grid as a
**secondary** data source — for carry/roll, as a hedge instrument, and (the acceptance bar) as a
**cross-check** on the option-implied forward — **without ever displacing the derived forward as
primary.** The forward stays backed out of the option chain via put–call parity
(`ForwardCurvePoint`); futures are an independently-sourced confirmation that the derived forward
is sound, captured and stamped like any other raw input, and reconciled within a documented
tolerance.

Tenor grid (pinned, P0.1 / OQ-4): **10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y**.

## What to do (ordered)

1. **Land the blueprint amendment + `FuturesPoint` ADR first — this is the first task, and it is
   not code.** Author and accept an **ADR** and a **merged blueprint amendment** that define the
   listed-futures product (which exchanges/contracts, roll convention, settlement, day count, and
   how a listed contract maps onto a pinned tenor). The owner ruling (2026-06-17) supplies the GO;
   this step **produces** the contract artifacts, it does not wait on someone else to. Do not write
   the contract code ahead of the blueprint amendment; ADR 0011 makes the blueprint the reference,
   and a contract that precedes it is the divergence this discipline exists to prevent.
2. **Decide the contract: extend `ForwardCurvePoint` vs. new `FuturesPoint`.** Prefer a **new
   `FuturesPoint`** (D1 already reserves the name as a gated 1D contract): the captured futures
   leg has fields the derived forward does not (listed contract identifier, exchange, settlement
   type, roll/expiry of the *listed* contract vs. the *pinned tenor* it maps to) and conflating
   captured-vs-derived on one record loses the primary/secondary distinction. Keep it frozen,
   slotted, with a `ProvenanceStamp`, mirroring the `ForwardCurvePoint` shape (`snapshot_ts`,
   `underlying`, `maturity_years`, `expiry_date`, `day_count`, the captured futures price, plus the
   listed-contract metadata). Add the additive registry entry; round-trip through A's adapter.
3. **Capture the futures points (secondary).** Add the listed-futures capture path that lands one
   provenance-stamped `FuturesPoint` per `(underlying, tenor)` per close snapshot, with an **explicit
   primary key** `(provider, underlying, trade_date, maturity_years)` (declared, not implied), partitioned per
   D1 (`provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]`). Captured raw — no derivation,
   no smoothing. Map each listed contract onto the pinned tenor grid per the blueprint amendment's
   rule from task 1.
4. **Cross-check forward vs. futures.** Add the reconciliation that, for each `(underlying, tenor)`,
   compares the **captured** `FuturesPoint` against the **derived** `ForwardCurvePoint` and emits a
   labeled diagnostic when they diverge beyond a configured tolerance (tolerance is config, per
   C7 / ADR 0028 — never a `.py` literal). The derived forward remains primary; the futures point
   never overwrites it. A breach is a **flagged triage record, not an exception** (it feeds 1H QC).
5. **No look-ahead.** Every cross-check and any carry/roll derived from the structure uses only the
   close snapshot in force as-of that `trade_date`; never join today's futures curve onto a past
   date. Run `check-lookahead-bias` over the capture + reconciliation path.

## Test surface

Read `tasks/TESTING.md`. Independent oracles are mandatory; expected values come from a source
other than the code under test. Specific cases:

- **Contract round-trip (D → A seam).** A `FuturesPoint` writes and reads back **equal** through A's
  adapter and validates against the registry schema; at least **one malformed instance** (missing
  the pinned-tenor mapping, empty `provider`, non-grid `maturity_years`) is **rejected with an
  explicit error**, not silently coerced.
- **Cross-check tolerance — independent oracle.** Hand-construct, in the test comment, an
  option-implied forward `F_opt` (from a call/put parity pair, per TESTING.md's Forward oracle) and
  a captured futures price `F_fut`; assert the reconciliation passes when `|F_fut − F_opt|` is
  inside tolerance and emits the **labeled divergence diagnostic** (not an exception, not a bare
  NaN) when outside. Test the value **exactly on the tolerance boundary** both sides.
- **Tenor-grid coverage.** Captured points land on the pinned grid `{10d,1m,3m,6m,12m,18m,2y,3y}`
  only; an off-grid tenor is rejected, and a **missing** tenor surfaces as a coverage gap (feeds 1H
  QC), not a silent hole.
- **Partition disjointness (D1 invariant).** Two providers' futures for the same
  `(underlying, trade_date)` land in **disjoint** partitions and never mix; a `read` without a
  `provider` filter does not merge them.
- **Provenance.** Every `FuturesPoint` carries a non-empty, well-formed `ProvenanceStamp` with the
  capture config hash (E's cross-cutting invariant).
- **No look-ahead.** A reconstruction for past day D uses only D's snapshot; a test that injects a
  later futures curve must **not** change D's cross-check result. `check-lookahead-bias` clean.
- **Edge cases (the floor).** Empty futures set, single tenor, duplicate listed contract mapping to
  one tenor, NaN/inf futures price — each a labeled failure, never a crash or silent pass.
- Gate green: `ruff && mypy && lint-imports && pytest`.

## Done criteria

P0.4 landed GO (accepted ADR + merged blueprint amendment) **before any code**; a `FuturesPoint`
contract exists, round-trips through A, and is registered additive-nullable; listed-futures points
are captured as **secondary**, provenance-stamped, partitioned per ADR 0034 §4 with `provider`
first; a forward-vs-futures cross-check reconciles the captured futures against the **primary**
derived forward within a configured tolerance and flags breaches as triage records; the derived
forward is never displaced; no look-ahead; root gate green. **If listed-futures data turns out not
to be obtainable from IBKR for a given tenor, that tenor surfaces as a coverage gap (feeds 1H QC) —
the derived forward already covers it; absence is not a defect.**

## Gotchas

- **The gate is the whole point.** The single largest failure mode here is building the contract
  "because it's a one-liner" before the blueprint amendment exists. Don't. ADR 0011 makes the
  blueprint the reference; a futures contract that precedes the amendment is exactly the drift the
  gate prevents. Task 1 is a stop-or-go, not a formality.
- **Primary vs. secondary is a domain invariant, not a preference.** The forward is *derived* and
  *primary* (put–call parity, `ForwardCurvePoint`); futures are *captured* and *secondary*. The
  cross-check reads the futures to confirm the forward — it never lets the futures overwrite,
  smooth, or seed the forward. A breach flags; it does not correct.
- **Listed contract ≠ pinned tenor.** Exchanges list discrete expiries; the grid is fixed at
  `10d…3y`. The mapping/roll rule must come from the P0.4 blueprint amendment, not be invented
  here — record which listed contract backs each tenor on the row, and keep it auditable.
- **Off the critical path — keep it there.** 1A→1I must ship and pass with forward-only. Do not let
  1D become a dependency of any of them; it is a parallel cross-check, and its absence is not a
  defect in the main path.
- **Config, not literals.** Tolerance, the provider/exchange set, and the roll rule are validated
  typed config (C7 / ADR 0028); `version` is a label, not a reproducibility input.
- **uv only** for any environment/dependency work.
