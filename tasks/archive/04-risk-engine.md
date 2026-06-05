# Workstream D — Risk engine

- **Branch:** `feat/risk-engine`
- **Owns:** `src/risk`.
- **Roadmap coverage:** steps 11 (Greeks and per-position risk) and 12 (scenario engine).
- **Depends on:** A (contracts, fixtures), C (the frozen pricing interface — Greeks and reprice run through it). Can start against the pricing contract using fixtures before C is fully done.
- **Blocks:** E (orchestration runs risk in the daily sequence; the actor publishes its outputs).

## Objective

This is the payload the whole backbone exists to produce: trustworthy portfolio
risk and stress PnL. Like C, it is pure functions over A's contracts, building on
C's pricing engine. It is a separate workstream from C precisely because solid
risk is the goal and deserves a dedicated owner. It encodes no strategy logic —
only generic risk control, capacity, and margin-style diagnostics.

## What you build

1. **Greeks and per-position risk** (step 11). Define the sensitivity set at
   instrument and portfolio level. Compute first- and second-order Greeks (delta
   Eq 13, gamma Eq 14, vega Eq 15, theta Eq 16) in one unified, explicit unit
   system, analytic where the pricer exposes them, finite-difference with versioned
   documented bump sizes otherwise. Monetize: dollar gamma (Eq 17), dollar vega
   (Eq 18) with the correct multiplier and currency. Join `Position` to the
   analytics snapshot, compute per-line price/Greeks/monetized sensitivities, then
   aggregate by instrument, maturity, underlying, and any desk grouping key.
   Reconcile against broker-returned Greeks where available; surface discrepancies
   beyond threshold automatically. Store both line-level and aggregate outputs —
   debugging always starts at the line.

2. **Scenario engine** (step 12). Versioned scenario grids treated as explicit
   market states, not Greek multipliers: parallel spot moves, parallel vol shifts,
   a combined spot-and-vol stress, a small time roll-down. Reprice the full
   portfolio under every scenario (full reprice is the source of truth) and also
   offer the local Greeks-based approximation (Eq 19) for fast intraday checks.
   Attribute PnL by line, underlying, and scenario family; compute worst-case loss
   and top contributors. Persist the exact scenario grid version alongside every
   result so a report regenerates exactly from positions + snapshot + scenario
   version.

## Acceptance criteria

- The same positions on the same analytics snapshot always produce the same
  aggregate risk; dollar gamma/vega conventions are documented and stable.
- Greek sanity: analytic and central-difference Greeks agree within tolerance on
  the reference contracts (this check exists even when analytic Greeks are used —
  it catches sign and unit errors).
- Reconciliation discrepancies beyond threshold are surfaced automatically.
- A scenario report regenerates exactly given positions, snapshot, and scenario
  version; worst-case contributors are explainable; full reprice and the local
  approximation agree within documented limits for small shocks.
- All configured scenarios execute and store with no missing results.

## Test surface

Cross-cutting rules — independent oracles, property tests, the edge-case and
coverage floors on this pure core — live in [TESTING.md](TESTING.md). Read it
first. You build against C's frozen pricing interface using A's fixtures, so a
pinned interface test (below) is what keeps a C-side change from surfacing as a
mystery in E.

Greeks and per-position risk:
- Analytic vs central-difference Greeks agree within tolerance on the reference
  contracts — and this test exists even where analytic Greeks are used, because
  it is what catches a sign or unit error (delta Eq 13, gamma Eq 14, vega Eq 15,
  theta Eq 16).
- Monetization: a contract with multiplier 100 produces a dollar gamma (Eq 17)
  and dollar vega (Eq 18) exactly 100× the per-unit value, in the right currency.
- Aggregation: sum of line-level equals the aggregate for a hand-summed 2–3
  position fixture, by instrument, maturity, and underlying; a long+short of the
  same contract nets to ~0; aggregate is invariant under position reordering
  (property test, TESTING.md).
- Bump-size consistency: assert Greeks and the scenario engine draw the bump size
  from one shared versioned source — the gotcha made into a test, so they cannot
  silently diverge.
- Reconciliation: a broker Greek beyond threshold is surfaced automatically; one
  within threshold is not.
- Edge cases: empty portfolio, single position, multi-currency aggregation, a
  position on a contract C flagged low-confidence.

Scenario engine:
- Full reprice is the source of truth: full reprice and the local Greeks-based
  approximation (Eq 19) agree within the documented limit for small shocks and
  are asserted to diverge beyond it for large ones — both directions tested.
- Worst-case loss and top contributors match a hand-worked known portfolio.
- Reproducibility: a report regenerates byte-identically from positions +
  snapshot + scenario version (a golden test, TESTING.md); the scenario grid
  version is persisted alongside every result and is queryable.
- Completeness: every configured scenario executes and stores — assert the result
  count equals the grid size, no missing cells.

Seam tests (you own them, per TESTING.md): `Position`, `RiskAggregate`,
`ScenarioResult` round-trip through A's adapter and carry a provenance stamp; a
test pins C's pricing-interface shape so a C-side change breaks here, not in E.

## Invariants you own

Determinism and provenance on every risk and scenario output. The scenario grid
is versioned, part of the data lineage, and queryable alongside its results —
never a mutable notebook cell. Reproducibility of worst-case loss under a pinned
scenario version is the headline guarantee.

## Gotchas

Inconsistent bump sizes across modules are a classic hidden error that makes risk
and scenario disagree for reasons unrelated to economics — version bump sizes in
one place. Keep full reprice as the reference even where the approximation is
faster; the approximation is a convenience, not the truth.
