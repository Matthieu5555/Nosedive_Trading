# 0021 — Analytics core merge (M2): one survivor per module, pricing interface frozen

- **Status:** accepted
- **Date:** 2026-06-05

## Context

This is workstream **M2** of the merge with Vincent's AlgoTrading (see
`tasks/M2-analytics-core.md`, `tasks/TASKBOARD.md`). Both repos built the same pure
analytics stack — snapshots → forwards → IV → surfaces → pricing, plus shared
numerical utils — module for module. M2 is the bake-off: diff the two per module, keep
the better one (correctness first, then depth, then clarity), and land **one**
implementation per module under `packages/infra/src/algotrading/infra/{...}`, bound to
the seams M0 froze (`algotrading.core` config/provenance, `algotrading.infra.contracts`
dataclasses). Unlike M6, M2 is built directly in the `packages/` monorepo — M0 had
landed, so the analytics modules fill the skeletons M0 scaffolded.

The blueprint is the plan of record (ADR 0011): where two implementations disagree on a
number, the independent oracle decides and the code is fixed to the blueprint, never the
reverse.

## Decision — the per-module winners

- **utils → Vincent's day-count, a merged robust module.** `daycount.py` is adopted
  from Vincent verbatim (the single day-count source; our side had it scattered).
  `robust.py` unifies both sides into one home, in **`float`** (not Vincent's
  `Decimal`): the analytics pipeline it feeds is float throughout and its determinism is
  anchored by the golden + cross-process-hash machinery, not by decimal exactness —
  mixing `Decimal` into a float hot path would lose that benefit and add friction. It
  carries Vincent's `robust_zscores`, `robust_zscore_vs_baseline`, and `weighted_median`
  plus our `theil_sen_line` and the floored `outlier_flags`. There is **one**
  MAD-rejection primitive — our `outlier_flags` (it has a noise floor that keeps a
  near-perfect fit from flagging every clean strike); Vincent's `reject_outliers` was
  dropped as the redundant unfloored variant, per "no two implementations of the same
  formula." `theil_sen_line` is now generic and raises `ValueError` (not the
  forward-specific `DegenerateParityFit`) since it left the parity module.

- **snapshots → ours.** Our `MarketStateSnapshot` *is* the frozen contract shape
  (per-instrument: bid/ask/last/spread_pct/reference_type/flags/completeness), and quote
  QC is wired into the build path (`assess_snapshot`, the `usable`/full split on
  `SnapshotBatch`) — the spec requires keeping that. Vincent's snapshot is an in-memory
  aggregate that does not match the seam.

- **forwards → ours, oracle-verified.** Joint weighted-least-squares recovery of F and
  DF off the parity line, with Theil-Sen + MAD outlier rejection. The independent oracle
  (the synthetic generator: F=100, DF=0.99 priced via Black-76) is recovered to ~1e-9,
  so correctness is proven against a different code path. The robust kernels now come
  from `utils.robust`; the parity-specific WLS (`regress_forward_and_discount_factor`,
  `parity_forward_from_pair`) stays in `forwards.parity`.

- **iv → ours.** Bracketed Brent (`scipy.optimize.brentq`) with two independent oracles
  (pricer-inversion *and* `py_vollib`'s "let's be rational"), and an engine-agnostic
  primitive (`solve_implied_vol_scalar`) that inverts any monotone pricer. Every outcome
  is a labeled status (`converged`/`below_intrinsic`/`above_max`/`non_convergence`),
  never a bare NaN — which already satisfies the spec's "adopt Vincent's structured
  reason codes" intent.

- **surfaces → ours.** SVI (Eq 20) with closed-form derivatives, a labeled nonparametric
  fallback when sparse, and **both** no-arb checks: calendar (Eq 21) and the **butterfly
  via Gatheral's g(k)**, which Vincent's surface lacks. Our `arbitrage.py` therefore
  supersedes Vincent's `diagnostics.py` (it is strictly more). What we adopt from Vincent
  is his **golden-fixture / end-to-end test discipline**: determinism is proven on the
  whole pipeline (golden artifact + cross-process stamp hash + reordering invariance),
  and the SVI generator is the surface oracle (recover known params within tolerance).
  His JSON golden fixtures were **not** ported wholesale — their schema diverges from our
  pipeline and our known-parameter synthetic oracle is the stronger check.

- **pricing → ours.** Closed-form Black-76 European + a QuantLib **Leisen-Reimer**
  American lattice (not Vincent's hand-rolled CRR). We keep `PRICER_VERSION =
  "black76-lr-1.0.0"`, the property tests (parity, gamma/vega ≥ 0, delta bounds, price
  monotone in vol, American ≥ European), the forward-consistency check at construction,
  and the determinism golden + cross-process hash.

## Frozen seam — pricing interface for M3

`algotrading.infra.pricing` is frozen here for M3 to build risk on: `PricingState`
(`from_forward`/`from_spot`), `PriceGreeks`, `price`/`price_european`/`price_american`,
`pricing_result`, and `PRICER_VERSION`. A shape pin-test guards it so a pricing-side
change breaks M3's suite loudly rather than silently.

## Shared fixture library

The rogues'-gallery + known-answer generators (`fixtures.{synthetic,quotes,library,
events,records}`) landed at `packages/infra/tests/fixtures`, importable by name across
the analytics/risk/qc tests via a pytest `pythonpath` entry. M2 is the first consumer;
M3 adds `fixtures.positions` (it needs the risk module). This is a provisional home for
the shared kit — M0/M9 may later formalize it as a test-support package.

## Verification

Gate green on the M2 surface: `ruff` and `mypy` clean on the six modules + utils + all
M2 tests; `import-linter` KEPT; **235 tests pass**; branch coverage **98.96%** (floor
90%). Golden + cross-process-hash determinism reproduces the flat build's artifact
**byte-for-byte, stamp hashes included** — evidence the port preserved exact behavior.
The C→A seam test round-trips every derived contract through `ParquetStore` and validates
its provenance stamp.

Two failures remain in the workspace-wide gate, both **outside M2** in other agents'
uncommitted work: a `ruff` F821 in `apps/frontend` (M8) and a `mypy` return-type error
in `storage/json_io.py` (M1/M10). Left for their owners.

## Gotchas honored

No "keep both and pick at runtime": the bake-off ended in one survivor per module. The
math stayed pure (no I/O, clock, or RNG). No golden number was moved to make a test pass
— the port reproduced the existing golden exactly.
