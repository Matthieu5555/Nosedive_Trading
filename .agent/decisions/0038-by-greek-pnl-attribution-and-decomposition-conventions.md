# 0038 — By-Greek PnL attribution: the `ScenarioAttribution` seam and its decomposition conventions

- **Status:** accepted, 2026-06-07. Lands **WS 2C** ([`../../tasks/2C-pnl-attribution.md`](../../tasks/2C-pnl-attribution.md)).
- **Date:** 2026-06-07.
- **Implements:** blueprint **Eq 19** (local PnL approximation from Greeks,
  `02-math-framework.md`) under the blueprint-is-authority rule
  ([ADR 0011](0011-blueprint-as-plan-of-record.md)). Adds no math the blueprint does not
  state — it factors the four-term Taylor expansion the blueprint already names.
- **Relates to:** [[0006-risk-engine]] (the valuation seam; the full reprice is truth),
  [[0029-contract-field-names-conform-to-blueprint]] (`dollar_*`/`*_pnl`, never `cash_*`),
  [[0036-dollar-greek-units-and-monetization-conventions]] (the `gamma_normalisation` /
  `theta_day_count` convention forks this borrows the vocabulary of),
  [[0028-configuration-and-reproducibility-standard]] (config-DI + `config_hashes`).

## Context

The scenario engine already produced the two numbers an attribution view sits between: the
**full reprice** (`risk/scenarios.py` `full_reprice_pnl`, the ADR-0006 oracle) and a single
lumped **Taylor** number (`local_approx_pnl` / `_taylor_pnl`, Eq 19). What was missing is the
*explanation* — the split of that one Taylor number into its named per-Greek contributions
(`Δ·dS`, `½Γ·dS²`, `Vega·dσ`, `Θ·dt`) and the **residual** against the full reprice, so the
operator front (1I) can render a Δ→Γ→Vega→Θ→residual→full **waterfall**.

The blueprint data dictionary (Part IX) names no attribution table and no cross term (no
vanna/volga); Eq 19 is exactly the four-term expansion. So this increment adds a new derived
contract and a new pure builder, conforming to blueprint conventions for the new field names.

Two questions had to be ruled, and are recorded here so the next agent does not reverse-
engineer them:

1. **One home for the term math, or two?** The lumped path and the split must never drift.
2. **What do the gamma/theta "convention flags" do to a *PnL* term?** ADR 0036 owns the
   `gamma_normalisation` (one_pct/one_dollar) and `theta_day_count` (365/252) forks for the
   *displayed dollar Greeks*. A PnL contribution is, in total, convention-**invariant** (the
   money made is the money made), so naively applying a display rescale to a PnL term is a
   category error. But 2C asks for config-driven flags that move a term.

## Decision

**1. One home for the term arithmetic.** `taylor_terms(greeks, *, spot, scale, scenario,
config)` in `scenarios.py` is the single home; `_taylor_pnl` (hence `local_approx_pnl`) now
**delegates** to `taylor_terms(...).total`. The split therefore *is* the lump, by
construction — the `test_terms_sum_to_lumped_taylor` refactor-equivalence test holds
exactly, and a second copy of the formulas cannot exist to drift.

**2. A new `ScenarioAttribution` contract** in `contracts/tables.py` + registry (derived
layer, provenance + `source_snapshot_ts` required). It carries the named dollar
contributions, the lumped `approx_pnl`, the `full_reprice_pnl` oracle, the `residual`, the
`within_tolerance` verdict and the echoed tolerances, the scenario + attribution versions,
and a `level` (`position`/`book`). A book record carries the `__book__` sentinel in
`contract_key`, so per-line and book records never collide in the primary key. Field names
follow the blueprint `*_pnl` convention (ADR 0029); **never** `cash_*`.

**3. The gamma/theta flags are reporting normalisations on the *decomposition*, not on the
truth.** They live on a new `AttributionConfig` (the attribution section of `RiskParams`,
config-DI per ADR 0028, entering `config_hashes`). Their **defaults reproduce the blueprint
Eq-19 lump exactly** (`gamma_normalisation="one_dollar"` → ½Γ(dS)²; `theta_day_count=365` →
the grid's own calendar day-count). Flipping a flag rescales **only that one term** — `one_pct`
÷100 on the gamma term (mirroring `dollar_gamma`'s 1%-vs-$1 relationship), `252` ×365/252 on
the theta term — and the **residual absorbs the difference against the immutable full
reprice**. The full reprice is never touched; it stays the ADR-0006 oracle. This is the
deliberate divergence from a strict reading where a PnL term is convention-invariant: we let
the flag move the reported term so a desk can present the contribution in its house
convention, and we keep the honest-accuracy guarantee by always reporting the residual
against the truth.

**4. The residual is always reported, bounded but not gated.** Accepted when
`|residual| ≤ max(abs_tol, rel_tol·|full_reprice|)`; a large-shock residual is *material and
labeled*, not an error (Taylor is *expected* to diverge — the divergence is the headline). A
non-finite contribution or reprice is a labeled diagnostic, not silent agreement (mirrors
`reconciliation.py`'s NaN guard).

The **across-Greeks** axis here is orthogonal to the existing **across-positions**
`UnderlyingAttribution`/`FamilyAttribution`; the dataclass/builder shape is reused, the types
are not overloaded.

## Consequences

- A new persisted table `scenario_attributions` joins the frozen contract surface; its
  Parquet schema derives from the dataclass, round-trips through `ParquetStore`, and is
  write-ahead-validated like every derived contract. Golden output is byte-identical across
  processes (no `PYTHONHASHSEED` reliance).
- `_taylor_pnl`'s internal float grouping changed (each term `·scale` then summed, rather
  than summed then `·scale`); the lumped value moves by ≤ rounding. Nothing persisted depends
  on it (only the full reprice lands in storage), and every existing tolerance-based test
  still passes.
- The gamma/theta-flag semantics are a documented decomposition convention, **not** a claim
  that day-count changes realized PnL. Anyone extending this (e.g. adding a blueprint cross
  term) follows Eq 19 and this ADR, and leaves the full reprice as the oracle.
- 1I renders the waterfall off this seam without re-deriving anything; it owns the
  React/Plotly, this task owns the seam shape.
