# 2C — PnL attribution by Greek: decompose dPnL into Δ/Γ/Vega/Θ contributions

> **STATUS (2026-06-14): LANDED — premise stale, do NOT re-build.** The full implementation shipped
> 2026-06-07 (commit `4e3f50f`, "by-Greek PnL attribution + ScenarioAttribution seam (2C)"):
> `risk/attribution.py` (`LineAttribution`/`BookAttribution`, the residual/verdict, the seam
> projection) + `scenarios.py` `TaylorTerms`/`taylor_terms()`. It was later extended (2026-06-13)
> with Rho/Vanna/Volga + realized day-over-day. **Before touching this task, verify the 2C
> done-criteria are met** (golden fixtures, seam round-trip, config-hash provenance green) and
> **rescope to only the genuinely missing pieces, or close it** — the body below describes a build
> that already exists.
>
> **Phase 2, parallel-OK — the front (1I) is the priority, this is wiring on top of built risk.**
> The risk engine already produces the two numbers this view sits between: the **full reprice**
> (`risk/scenarios.py` `full_reprice_pnl`, the ADR-0006 source of truth) and a single lumped
> **Taylor** number (`local_approx_pnl` / `_taylor_pnl`, Eq 19). What is missing is the
> *explanation*: split that one Taylor number into its named **per-Greek contributions**
> (Δ·dS, ½Γ·dS², Vega·dσ, Θ·dt, …) and report the **residual** against the full reprice. The
> blueprint (ADR 0011) overrides this spec on the decomposition convention, the term set, and the
> sign of every term; where this file and the blueprint disagree, the blueprint wins.

- **Owns:** the attribution decomposition in `packages/infra/src/algotrading/infra/risk/` — a new
  per-Greek split that factors today's `_taylor_pnl` (`scenarios.py`) into named terms, a
  per-position and per-book **attribution view** (a frozen dataclass + a pure builder, same shape
  as `ScenarioLinePnl` / `ScenarioReport`), the residual-vs-full-reprice computation and its
  tolerance, the attribution config (which terms, the tolerance, the gamma/theta conventions), and
  the tests + golden fixtures. The front seam (a `ScenarioResult`-style projection the BFF/1I reads)
  and a Plotly attribution view (waterfall) are 1I's to render — this task owns the seam shape, not
  the React. Conforms to **[ADR 0006](../.agent/decisions/0006-risk-engine.md)** (valuation seam,
  full reprice is truth), **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)** (the
  attribution math **and** the `dollar_*` field names per `documentation/blueprint/09-data-dictionary.md` —
  never `cash_*`), and **[ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)**
  (Plotly/shadcn for the front view).
- **Depends on:** **infra/risk** (built — `full_reprice_pnl`, `local_approx_pnl`, `_taylor_pnl`,
  `PositionRisk.dollar_*`, `scenario_line_pnls`, `build_scenario_report` all exist and land in the
  frozen `ScenarioResult`); **2A** (the basket / multi-leg position set this attributes a book over)
  and **2B** (the reprice/scenario grid the dPnL is measured under). 2A/2B are Phase-2 siblings with
  no task files cut yet — attribution must not re-derive a basket or a scenario, it consumes theirs.
- **Blocks:** nothing structurally. It feeds **1I** (the attribution waterfall on Tab 2) but the
  front-first gate means 1I's page-1 work leads; 2C lands the seam 1I's Tab-2 reads.
- **State going in (audited 2026-06-07):** `risk/scenarios.py` has `full_reprice_pnl` (oracle) and
  `_taylor_pnl`, which **already sums the four terms** `delta·d_spot + ½·gamma·d_spot² +
  vega·vol_shock + theta·time_shock` but **returns only the lumped total** — the per-term breakdown is
  computed and thrown away. `ScenarioLinePnl` carries `full_reprice_pnl` + `approx_pnl` (no residual,
  no per-Greek split). `ScenarioReport` / the `UnderlyingAttribution` / `FamilyAttribution` views
  attribute the *full-reprice* PnL **across positions/underlyings/families** — that is a different
  axis from this task's **across-Greeks** decomposition; reuse their shape, don't conflate the axis.
  The dollar Greeks (`PositionRisk.dollar_delta/gamma/vega/theta`, Eq 17/18) live in `risk/greeks.py`.
  No by-Greek attribution view and no residual report exist anywhere.

## Objective

For one position and for one book, under one scenario (a 2B shocked state) or one realized dPnL
step, produce a deterministic **attribution record** that decomposes the dPnL into its named Greek
contributions — at minimum delta, gamma, vega, theta, and a cross/higher-order remainder — and
reports the **residual** of `sum(contributions)` against the **full reprice** (the ADR-0006 oracle).
The full reprice is the truth; the Greek decomposition is the explanation, and its accuracy is the
residual. Each contribution is monetized in **dollar terms, book-additive**, using the dollar-Greek
convention already in `risk/greeks.py` (P0.2 / 1F), so a book's attribution is the sum of its lines'
attributions term by term. Output is a frozen, stamped record per position and an aggregated record
per book; both project into a `ScenarioResult`-style seam the BFF / 1I read.

The decomposition convention (blueprint Eq 19 / ADR 0011 — **the blueprint overrides if it differs**):

- `delta_pnl  = Δ · dS · scale`              (first-order spot)
- `gamma_pnl  = ½ · Γ · dS² · scale`         (second-order spot — **config: the dS² normalization**)
- `vega_pnl   = Vega · dσ · scale`           (first-order vol)
- `theta_pnl  = Θ · dt · scale`              (time roll-down — **config: 365 vs 252 day-count**)
- `residual   = full_reprice_pnl − (delta_pnl + gamma_pnl + vega_pnl + theta_pnl + …)`

where `dS = spot · spot_shock`, `dσ = vol_shock`, `dt = time_shock`, and `scale` is the line's
`multiplier · quantity` — exactly the quantities `_taylor_pnl` already uses, so the lumped Taylor
total **must equal** the sum of the split terms (this is a refactor-into-terms, not a new model).
Use the **ADR-0029 `dollar_*` names** for the monetized fields (the contributions are dollar PnL,
book-additive). The dS²-normalization and theta-day-count flags flow from validated config (C7
pattern), not `.py` literals, and enter the provenance `config_hashes`. Reconcile the term set and
the sign of every term to the blueprint data dictionary — if it names a cross term (e.g. vanna ·
dS · dσ, volga · dσ²) or a different theta sign, follow it and note the divergence.

## What to do (ordered)

1. **Factor `_taylor_pnl` into named terms.** Add a pure function in `risk/scenarios.py` (or a new
   `risk/attribution.py` that imports the term arithmetic from one home) that returns the four (or
   blueprint-named) per-Greek contributions **and** their sum — never a second copy of the term
   formulas. Assert by test that `sum(terms) == local_approx_pnl(line, scenario)` exactly (same
   inputs, same arithmetic, refactor-equivalence), so the split cannot silently diverge from the
   lumped Taylor path. Keep one home for the term math.

2. **Define the per-position attribution record.** A frozen dataclass (slots), same shape discipline
   as `ScenarioLinePnl`: the scenario (or dPnL step), the line, the named dollar contributions
   (`delta_pnl, gamma_pnl, vega_pnl, theta_pnl` + any blueprint cross term), the `full_reprice_pnl`
   (oracle), and the `residual`. The contributions use the ADR-0029 `dollar_*` convention and are
   book-additive. Carry the attribution config version/hash so "what terms, what tolerance" is in the
   lineage.

3. **Aggregate to the book.** A pure builder that sums the per-position records **term by term** into
   one book record (delta_pnl summed, gamma_pnl summed, …, residual summed), preserving the
   per-position breakdown. This is the **across-Greeks** axis — keep it orthogonal to the existing
   **across-positions** `UnderlyingAttribution` / `FamilyAttribution`; a book can be sliced both
   ways. Aggregation must be invariant under input-position reordering (the D-owned risk invariant).

4. **Residual + tolerance.** Compute `residual = full_reprice − sum(contributions)` per position and
   per book, and a **bounded, reported** residual check: the attribution is *accepted* when
   `|residual| ≤ tolerance` (absolute and/or relative, config-driven), and the residual is **always
   reported**, never silently dropped — for a large shock the Taylor decomposition is *expected* to
   diverge (the full reprice stays the truth) and that divergence is the headline number, labeled,
   not an error. A non-finite full reprice or contribution is a labeled diagnostic, not silent
   agreement (mirror `reconciliation.py`'s NaN guard).

5. **Config (C7 DI).** The term set, the dS²-normalization flag, the theta day-count (365 vs 252),
   and the residual tolerance flow from validated typed config injected into the pure builder — no
   YAML read deep in compute — and enter the stamp `config_hashes`. Reuse the existing
   `ScenarioConfig` / `RiskParams` home; add an attribution section rather than a new loader.

6. **Front seam.** Project the book attribution into a `ScenarioResult`-style typed surface the BFF
   reads (the existing risk seam — `scenario_result` is the pattern), carrying the named
   contributions + residual + tolerance verdict so 1I renders a **waterfall** (Δ → Γ → Vega → Θ →
   residual → full reprice) in Plotly per ADR 0030 without re-deriving anything. This task owns the
   seam shape and the contract round-trip; 1I owns the React/Plotly.

7. **Golden fixtures + regeneration command.** Commit golden attribution output for a small
   hand-checked book under a small and a large scenario (small: residual within tolerance; large:
   residual reported and material), with one documented, reviewable regeneration command. uv only
   (`uv run …`).

## Test surface

Read [TESTING.md](TESTING.md). The independent-oracle, golden-file, determinism, edge-case,
reordering-invariance, and seam rules there are mandatory. The cases specific to 2C:

- `test_terms_sum_to_lumped_taylor` — refactor-equivalence: for a fixture line+scenario,
  `delta_pnl + gamma_pnl + vega_pnl + theta_pnl` equals `local_approx_pnl(line, scenario)` exactly
  (same arithmetic, one home), so the split never drifts from the lumped path.
- `test_each_term_matches_hand_value` — independent oracle: for a fixture with known
  `(Δ,Γ,Vega,Θ,S,spot_shock,vol_shock,time_shock,mult,qty)`, each term equals the hand-computed
  `Δ·dS·scale`, `½·Γ·dS²·scale`, `Vega·dσ·scale`, `Θ·dt·scale` (values derived in the test comment,
  not read from the code under test), within float tolerance.
- `test_residual_is_full_reprice_minus_terms` — the residual equals `full_reprice_pnl − sum(terms)`,
  and for a **small** scenario `|residual| ≤ tolerance` (the decomposition explains the reprice);
  for a **large** scenario the residual is **material and reported, not an error** (Taylor diverges,
  full reprice stays truth).
- `test_book_attribution_is_term_wise_sum_of_lines` — independent oracle: hand-sum a 2–3 line book
  term by term; the book record equals the hand sum per term and the book residual equals the summed
  line residuals (book-additivity of the dollar contributions).
- `test_attribution_invariant_under_position_reordering` — shuffling the line set leaves the book
  attribution identical (the D-owned reordering invariant).
- `test_gamma_norm_flag` and `test_theta_daycount_flag` — flipping each config flag changes exactly
  that term by the expected factor and nothing else; the dollar names stay `dollar_*` (ADR 0029),
  never `cash_*`.
- `test_attribution_golden_byte_identical` — recompute the committed golden attribution (small +
  large scenario) and compare byte-for-byte; a separate-process hash check on the stamp
  `config_hashes` (no `PYTHONHASHSEED` reliance).
- `test_attribution_seam_round_trips` — the book attribution projects into the `ScenarioResult`-style
  surface, round-trips through the storage adapter, and validates against the registry schema (the
  D→A / risk-front seam); at least one malformed instance is rejected by write-ahead validation with
  an explicit error, not a silent coercion.
- Edge cases (the floor): empty book (zero lines → zero terms, zero residual, not a crash), single
  line, a zero-shock scenario (all terms zero, residual zero), a non-finite full reprice or Greek
  (labeled diagnostic, not silent agreement — mirror `reconciliation.py`), and a degenerate
  scale (`quantity == 0`).
- Branch coverage on the new pure attribution code at or above the committed floor (≥90%, the D
  `src/risk` threshold).
- Gate green: `uv run ruff … && uv run mypy … && uv run lint-imports && uv run pytest`.

## Done criteria

For one position and one book, under a 2B scenario, the engine emits a deterministic attribution
record decomposing dPnL into named dollar contributions (Δ/Γ/Vega/Θ + any blueprint cross term) plus
the **residual against the full reprice**; the split sums exactly to the existing lumped Taylor path
and book attribution is the term-wise sum of its lines; the residual is bounded-and-reported (within
tolerance for small shocks, material-and-labeled for large ones, with the full reprice as the
oracle); the gamma-normalization / theta-day-count flags are config-driven and enter `config_hashes`;
the contributions use the ADR-0029 `dollar_*` names and are book-additive; output matches the
committed golden fixtures byte-for-byte and is stable across processes and input reordering; the
attribution projects into a `ScenarioResult`-style seam that round-trips and that 1I renders as a
Plotly waterfall (ADR 0030); root gate green (uv only).

## Gotchas

- **Blueprint (ADR 0011) overrides** the term set, the dS² normalization, the day-count, and the
  sign of every term. If the blueprint data dictionary names a cross term (vanna `Δ·dS·dσ`, volga
  `½·∂Vega/∂σ·dσ²`) or a different convention, follow it and note the divergence — do not encode this
  file's four-term default over it.
- **The full reprice is the oracle, the decomposition is the explanation.** Never "improve" the
  attribution to make the residual zero by reverse-engineering it from the reprice — that defeats the
  point. The residual is the honest accuracy of the Greek story and is always reported.
- **Two attribution axes, kept orthogonal.** `scenarios.py` already attributes the full-reprice PnL
  **across positions/underlyings/families** (`UnderlyingAttribution`, `FamilyAttribution`). This task
  is **across Greeks**. Reuse the dataclass/builder shape; do not overload those types or conflate the
  axes — a book is sliced both ways independently.
- **One home for the term math.** The per-Greek arithmetic already lives inside `_taylor_pnl`. Factor
  it out once and have both the lumped path and the split call it; do not fork a second copy, or the
  refactor-equivalence test will (correctly) go red.
- **Dollar, book-additive, ADR-0029 names.** Contributions are monetized dollar PnL using the
  `risk/greeks.py` dollar convention so a book sums term by term; use `dollar_*`-style names, never
  `cash_*`.
- **`-0.0` / `10` vs `10.0` / `NaN` discipline** in the stamp hash (C7 hardening) — the golden
  attribution must be byte-identical across two processes without `PYTHONHASHSEED`.
- **Consume 2A's basket and 2B's scenario — do not re-derive them.** Attribution is the third layer:
  basket (2A) → reprice/scenario (2B) → explain (2C). If 2A/2B contracts are not yet frozen when this
  starts, pin against the built `PositionSet` / `Scenario` types and a fixture, and adjust at the seam
  when they land — but never re-implement a basket or a scenario grid here.
- **uv** for every Python command in tests, fixtures, and the gate; **npm** only for the 1I web app.
  No bare `python` / `pip`.
