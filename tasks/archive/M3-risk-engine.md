# M3 — Risk engine (Greeks, aggregation, scenarios, reconciliation)

- **Branch:** `feat/merge-risk`
- **Owns:** `packages/infra/src/algotrading/infra/risk/**` (+ README).
- **Depends on:** M0 (contracts), M2 (frozen pricing interface).
- **Blocks:** M7 (risk pipeline / actor), M8 (risk API).

## Objective

Merge the two risk engines into one, built on M2's frozen pricer. Both compute per-position Greeks, monetized sensitivities, portfolio aggregation, broker reconciliation, and scenario stress; Vincent adds basket/positions modeling. Bake-off per concern, keep the better, union the extra surface.

## The bake-off (ours vs Vincent's)

| Concern | Ours | Vincent's |
|---|---|---|
| Greeks/valuation | `backend/src/risk/{greeks,valuation,bumps}.py` | `infra/risk/snapshot.py` (+ pricing greeks) |
| aggregation | `backend/src/risk/aggregate.py` | `infra/risk/aggregation.py` |
| scenarios | `backend/src/risk/scenario.py` | `infra/risk/scenarios.py` |
| reconciliation | `backend/src/risk/reconciliation.py` | `infra/risk/reconciliation.py` |
| positions/basket | `backend/src/fixtures/positions.py` | `infra/risk/{positions,basket}.py` |
| config | (in code) | `infra/risk/config.py` |

## What to carry regardless of which side wins

- **Keep ours** (these were adversarially verified and fixed): the single shared **versioned bump source** + monetization convention + pricing adapter; the scenario grid that **de-dupes shocks and guards id collisions** (was 2×-counting the worst case on duplicate shocks); `effective_scenario_version` folding a hash of the grid-construction constants (so two grids can't share a version); reconciliation surfacing a **non-finite broker Greek**; the carry≠0 and non-100-multiplier regressions. See `backend/src/risk/**` + `test_{risk,scenario,risk_properties}.py` + golden `risk_pf_risk.json`.
- **Adopt from Vincent:** his `positions.py`/`basket.py` modeling and `config.py`-driven thresholds if richer than our fixture-based positions; his scenario report/grouping surface (`tests/risk/test_{scenario_report,grouping,basket}.py`).
- Land one bump source and one scenario-version mechanism — not two.

## Frozen seam

Consume M2's pricing interface only through its frozen signature — risk holds no pricing math of its own. Produce the `RiskAggregate` / `ScenarioResult` contracts (merge ours with his) for M7 to persist and M8 to serve.

## Test surface

Read [TESTING.md] first. Specific to M3:
- Greeks/monetization against the independent oracle (GBSM ≡ QuantLib ≡ py_vollib).
- Scenario grid: duplicate shocks do not double-count the worst case; two different grids cannot share a scenario version (assert the version hash bites).
- Reconciliation flags a non-finite broker Greek rather than silently passing.
- Determinism golden (carry ours: `risk_pf_risk.json`) + property tests (aggregation linearity, multiplier/carry handling).

## Done criteria

One risk package on M2's frozen pricer, our verified scenario/bump/reconciliation fixes preserved, Vincent's positions/basket depth folded in, gate green with oracle + determinism + property tests.

## Gotchas

The scenario de-dup and version-hash fixes are correctness, not taste — they go in. Don't let risk import a pricer implementation; it binds the interface only, or the "infra blind to alpha" + frozen-pricer guarantees rot. One `ScenarioResult` shape, agreed with M7 before you both build on it.
