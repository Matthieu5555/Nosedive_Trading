# 2D — Strategy composition: combine decorrelated sub-strategies into one book

> **Phase 2, the leaf of Tab 2.** 2D is the last of the four Tab-2 workstreams: it takes the
> positions 2A builds, the stress grid 2B reprices over, and the attribution 2C decomposes, and lays
> them into a single **book** — a layered set of sub-strategies whose Greeks are the additive sum and
> whose stressed PnL surface is the joint reprice. Phase 2 is **parallel-OK**, but the front (1I) is
> the priority; 2D builds only after its three siblings give it positions, a grid, and attribution.
> The blueprint (ADR 0011) overrides this spec on every formula, $-convention, and aggregation rule;
> where this file and the blueprint disagree, the blueprint wins.
>
> **3-onglets home (2026-06-17):** "Tab 2" = **Onglet 2 (Risque)** in the consolidated app — the
> composed book + combined $Greeks land in its **② Le book** / **④ Attribution** blocks
> ([frontend-3onglets-target-ux](frontend-3onglets-target-ux.md):60-73). The infra/BFF compute is
> unchanged; only the front home is renamed.

- **Owns:** a new book/composition layer under `packages/infra/src/algotrading/infra/risk/` (a *book
  view* that layers several 2A baskets/position sets into one and exposes combined net Greeks + a
  combined stressed PnL surface), the typed book/layer contract it persists (added to
  `packages/infra/src/algotrading/infra/contracts/tables.py` + registry/serialization in
  `contracts/registry.py`), the BFF compose/book endpoints in `apps/frontend/src/algotrading/frontend/`,
  the web composition + combined-view UI in `apps/frontend/web/src/`, the composition config (which
  sub-strategies, layer labels, display options), and the tests. Conforms to
  **[ADR 0006](../.agent/decisions/0006-risk-engine.md)** (risk aggregation is additive across
  positions), **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)** (blueprint
  governs the domain),
  **[ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)** (Plotly.js
  for the combined PnL surface; shadcn/ui + TanStack Table for the compose shell), and
  **[ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)** (storage).
- **Depends on:** **2A** ([2A-basket-builder.md](archive/2A-basket-builder.md) — the frozen `Basket`/`BasketLeg`
  contract + `risk/multileg.py` book-additive risk a book layers; **landed** `b2b6a06`), **2B**
  ([2B-stress-scenario.md](archive/2B-stress-scenario.md) — the ±50%/±50% spot×vol `StressSurfaceConfig` grid
  + the `GET /api/risk/scenarios` `surface` seam the combined PnL surface reprices over; **landed,
  full-stack**), and **2C** ([2C-pnl-attribution.md](archive/infra-pnl-attribution.md) — the per-Greek
  `ScenarioAttribution` shape the combined view drills into; **landed** `4e3f50f`). All three are
  specced **and landed**; 2D is their leaf — it **consumes their frozen contracts**, it does not
  re-derive them. Reuses the additive aggregation (`risk/aggregation.py` — sum of lines equals the
  aggregate, order-free, over per-unit `position_*`), the basket variance primitive (`risk/basket.py`,
  Eq 23), the scenario/full-reprice engine (`risk/scenarios.py`), the position model
  (`risk/positions.py`), and the **canonical dollar-Greek home** `pricing/dollar_greeks.py` (per-1%
  gamma / per-365 theta — *not* `risk/greeks.py`, which emits per-unit Greeks only). Inherits the
  book-additive $-Greeks 1F kept (`tasks/archive/1F-analytics-projection.md` Gotchas: "keep the
  dollar Greeks book-additive").
- **Blocks:** nothing — it is the leaf of Tab 2 and of Phase 2.
- **State going in (verified 2026-06-07):** infra risk aggregation **is** additive across positions
  and order-free (`risk/aggregation.py`: "the sum of the lines equals the aggregate and ... the
  aggregate does not depend on the order positions arrive in"; net sensitivities are contract-level
  `per_unit · multiplier · quantity`, so different multipliers sum coherently — ADR 0006). The
  scenario engine builds an explicit-state grid and full-reprices every position against base
  (`risk/scenarios.py`). The basket variance identity exists (`risk/basket.py`). **No book /
  strategy-composition layer exists** — nothing aggregates *across sub-strategies* into a named,
  layered book, and there is no combined PnL surface. 2A/2B/2C are **specced and landed** — cite
  their concrete frozen seams (do **not** re-derive): 2A's `Basket`/`BasketLeg` contract +
  `risk/multileg.py`, 2B's `StressSurfaceConfig` grid + the `/api/risk/scenarios` surface view,
  2C's `ScenarioAttribution`.

## Objective

Provide an operator a **book** — a named, ordered set of sub-strategies (each a 2A basket / position
set) layered into one — and two combined views over it: (1) the **combined Greeks**, which are the
**additive sum** of the sub-strategies' position-level net sensitivities (P0.2 / 1F book-additive
$-Greeks, ADR 0006); and (2) the **combined stressed PnL surface**, which is the sum / joint reprice
of the constituent positions across the **2B stress grid** (same spot × vol scenario family, one full
reprice over the union of all layers). The book is a *view* — it layers positions, it does not mutate
or re-solve them; a sub-strategy's own analytics are unchanged by being placed in a book.

"Decorrelated" is the **operator's intent**, not a computation 2D performs: the operator hand-picks
sub-strategies they believe are decorrelated and composes them. 2D delivers the **composition +
aggregation** (layer, sum the Greeks, joint-reprice the PnL) and a diagnostic that *shows* the
realised diversification (it may surface `risk/basket.py`'s `diversification_ratio` over the layers as
a read-only diagnostic). An **automatic decorrelation optimiser** — searching/weighting sub-strategies
to minimise correlation — is explicitly **out of scope** for 2D (see Gotchas / Not in this task).

Acceptance (roadmap): **book Greeks are additive across positions** (the combined net Greek for each
sensitivity equals the sum of the per-layer net Greeks, which equals the flat sum over the union of
all positions — three ways, one number), and the **combined PnL surface renders** (the joint reprice
over the 2B grid produces a finite surface the front draws via Plotly).

## What to do (ordered)

1. **Define the book / composition contract.** Add a frozen, slotted typed contract to
   `contracts/tables.py` for a book and its layers: a `book_id` / label, an **ordered** list of layer
   references (each a 2A basket / position-set identity + an operator label), the resolved combined
   net Greeks (decimal **and** dollar, side by side, unit-tagged — reuse the 1F/P0.2 dollar layer and
   unit strings, never a second copy), the per-layer net Greeks (so the combined view drills down to
   2C attribution), and a `provenance` stamp. Register it (registry + serialization round-trip) via
   the **additive** schema-evolution path. Keep dollar Greeks **book-additive** (the same property 1F
   was told to preserve). Mark it provider-agnostic in the D1 registry if persisted (a book is
   operator metadata over positions, not source-specific market data) — confirm against D1 before
   choosing.

2. **Build the combined-Greeks aggregation (pure).** A pure function
   `(ordered layers of position sets + their analytics) → combined net Greeks + per-layer net Greeks`
   that reuses `risk/aggregation.py` over the **union** of all layers' positions. The combined number
   for each sensitivity must equal both (a) the sum of the per-layer aggregates and (b) the flat
   aggregate over the union — assert this equivalence is exact (contract-level
   `per_unit · multiplier · quantity`, order-free, ADR 0006). Do **not** fork aggregation; layer it on
   the existing reducer. Dollar monetization stays at the line and is currency-tagged (do not sum
   across currencies — same rule `risk/aggregation.py` already enforces).

3. **Build the combined stressed PnL surface (pure).** A pure function
   `(union of all layers' positions + the 2B stress grid) → combined PnL surface` that **full-reprices**
   the union over the same spot × vol scenario family 2B uses (`risk/scenarios.py` — explicit shocked
   *states*, not Greek multipliers; full reprice differenced against base is source of truth). The
   combined surface at each grid node is the sum of the per-position scenario PnLs; assert it equals
   the sum of the per-layer surfaces (additivity of PnL across layers at every node). Keep the grid
   **config-driven** (ADR 0028) — it flows from the same `ScenarioConfig` 2B drives, never `.py`
   literals — and its construction goes in `config_hashes`.

4. **Wire the composition entrypoint.** A pure compose function
   `(book config = ordered sub-strategy selection + the stress grid config) → book contract`
   that resolves the named sub-strategies to their 2A position sets/analytics, calls (2) and (3),
   stamps the result with complete `config_hashes` (layer set, grid construction, gamma/theta flags),
   and returns the typed book — injected config (C7 DI pattern), no YAML read deep in compute. The
   actor/orchestration calls it and persists what comes back.

5. **BFF compose + book endpoints.** In `apps/frontend/src/algotrading/frontend/` add a router that
   (a) lists the available sub-strategies (2A baskets), (b) accepts an ordered composition and returns
   the book's combined Greeks + per-layer breakdown, and (c) returns the combined PnL surface for the
   composed book. No business logic in the router — it serializes the infra book contract and reads
   the pure compose function's output, the same router discipline 1I follows. Register it in `app.py`.

6. **Compose + combined-view UI.** In `apps/frontend/web/src/` add a **layering/compose** control
   (shadcn/ui shell + TanStack Table for the sub-strategy picker / layer list — add/remove/reorder
   layers, label them; ADR 0030) and a **combined view**: a Plotly surface for the combined stressed
   PnL and a dense table of combined + per-layer Greeks (dollar numbers rendered from their unit
   strings, never re-derived), with a drill-down to the 2C per-layer attribution. Reuse the 1I chart /
   surface / table components and BFF-client patterns (`api.ts`, typed clients) — Tab 2 reuses the
   front seams 1I builds clean.

7. **Golden fixtures + regeneration command.** Commit a golden book (a 2–3-layer composition over a
   small hand-checked fixture) with its combined Greeks and combined PnL surface, plus one documented
   regeneration command (deliberate, reviewable — never auto-regenerated). uv only for every backend
   command (`uv run …`); npm for the web build/test.

## Test surface

Read [TESTING.md](TESTING.md). The independent-oracle, golden-file, determinism, reordering-invariance,
seam, property-based, and edge-case rules there are mandatory; the cases specific to 2D:

- `test_book_greeks_equal_sum_of_layers` — **independent oracle**: for a hand-built 2–3-layer book
  whose per-layer net Greeks are summed **by hand in the test comment** (not read from the code under
  test), the book's combined net Greek for each sensitivity equals that hand sum, within float
  tolerance. Cover decimal **and** dollar Greeks.
- `test_book_greeks_equal_flat_union_aggregate` — the combined Greeks computed layer-then-sum equal
  the flat aggregate over the union of all positions (the three-ways-one-number identity), exact.
- `test_book_greeks_additive_property` — **property test** (Hypothesis): over random layer
  partitions of a random position set, summing per-layer aggregates equals the flat aggregate
  (additivity, ADR 0006 / TESTING.md "sum of lines equals aggregate").
- `test_book_composition_reorder_invariant` — reordering the layers (and the positions within them)
  leaves the combined Greeks identical (order-free, TESTING.md reordering-invariance); display order is
  the only thing the layer order changes, asserted separately if a defined display order is required.
- `test_combined_pnl_surface_is_sum_of_layer_surfaces` — at every node of the 2B grid, the combined
  stressed PnL equals the sum of the per-layer full-reprice PnLs (PnL additivity across layers); the
  surface is finite (no NaN/inf) and has the grid's shape — **the surface renders** acceptance, made
  numeric.
- `test_combined_pnl_uses_full_reprice_not_taylor` — the combined surface comes from the explicit-state
  full reprice (`risk/scenarios.py`), not a Greek-multiplier Taylor approximation; assert the two
  agree for a small shock and may diverge for a large one (the documented scenario invariant).
- `test_diversification_diagnostic_is_read_only` — the diversification diagnostic (if surfaced from
  `risk/basket.py`) is a reported number that does **not** alter any layer's positions, Greeks, or PnL;
  removing it changes nothing in the book's aggregates.
- `test_no_decorrelation_optimiser` — guard test: composing a book never reweights, drops, or reorders
  sub-strategies to reduce correlation; the operator's selection is honoured exactly (out-of-scope
  optimiser is genuinely absent, not silently present).
- `test_book_contract_roundtrip_and_stamp` — **seam (D→A / C→A)**: the book contract round-trips
  through the storage adapter and validates against the registry schema; it carries a complete,
  non-empty provenance stamp with `config_hashes` covering the layer set + grid construction; at least
  one malformed book instance is rejected by write-ahead validation with an explicit error, not a
  silent coercion.
- `test_book_config_hash_cross_process` — the book's `config_hashes` are byte-identical across two
  **separate** Python processes (subprocess check, no `PYTHONHASHSEED` reliance); a reorder/comment
  change to the composition config that does not change the economic selection leaves the hash
  identical, an actual change to the layer set or grid moves exactly its bundle's hash.
- Edge cases (the floor): an **empty book** (zero layers → zero Greeks, empty/degenerate surface,
  labeled — not a crash), a **single-layer book** (combined == that layer exactly), a **layer with
  zero positions**, duplicate sub-strategy references (defined behaviour — reject or sum, pick one and
  test it), and NaN/inf inputs rejected with a structured diagnostic.
- BFF/web seam: `apps/frontend/tests/test_readback_api.py`-style test that the compose router reads
  back the persisted book fields correctly; a web component test (Vitest/RTL) asserting the combined
  Greeks table renders the dollar unit strings and the Plotly combined-surface mounts with the BFF
  payload (user-facing assertions, per the write-tests skill).
- Branch coverage on the new pure composition module at or above the committed floor (≥90%).
- Gate green: `uv run ruff … && uv run mypy … && uv run lint-imports && uv run pytest` (backend);
  `npm run lint && npm run test && npm run build` (web).

## Done criteria

An operator composes a named book from an ordered set of 2A sub-strategies; the book's combined Greeks
are the **additive sum** across positions — provably equal three ways (per-layer sum, flat union
aggregate, hand sum) — in decimal **and** unit-tagged dollar form, book-additive; the combined
stressed PnL surface is the joint full-reprice over the 2B grid and **renders** (finite, grid-shaped,
drawn by Plotly), equal to the sum of the per-layer surfaces at every node; the book contract
round-trips through storage with a complete stamp and cross-process-stable `config_hashes`; the
compose/combined-view UI lets the operator layer, label, reorder, and drill into 2C attribution; no
decorrelation optimiser is present; root gate green (uv) and web gate green (npm).

## Gotchas

- **Blueprint (ADR 0011) overrides** every $-convention, Greek formula, and aggregation rule in this
  file. Follow the blueprint data dictionary; note any divergence, do not encode this file's defaults
  over it.
- **No second aggregation / dollar-Greek home.** Layer the book on `risk/aggregation.py` and reuse the
  `risk/greeks.py` dollar formulas + 1F unit strings — do not fork a parallel summation or a second
  dollar-Greek path. The additivity invariant ADR 0006 already guarantees is the whole point; don't
  re-derive it.
- **The book is a view, not a mutation.** Placing a sub-strategy in a book must not re-solve, reweight,
  or alter its positions or analytics — a layer's own numbers are identical inside and outside any book.
- **"Decorrelated" is intent, not computation.** 2D composes and aggregates what the operator picks; it
  does **not** search for or optimise decorrelation. Surfacing `risk/basket.py`'s diversification ratio
  as a read-only diagnostic is fine; an optimiser that changes the selection is out of scope.
- **Combined PnL is full reprice, not Taylor.** Use the explicit-state scenario reprice
  (`risk/scenarios.py`) over the union of positions — the Greek-multiplier shortcut diverges for large
  shocks and is not the book's PnL surface.
- **Don't sum dollars across currencies.** Monetization stays line-level and currency-tagged (the rule
  `risk/aggregation.py` already enforces); the additive aggregate carries raw net sensitivities.
- **Siblings landed — cite their frozen seams.** 2A/2B/2C are specced and on `main`; build 2D
  against their **actual** contracts (`Basket`/`BasketLeg` + `risk/multileg.py`; `StressSurfaceConfig`
  + `/api/risk/scenarios` surface; `ScenarioAttribution`), not against intent. Do not re-invent a
  seam they already froze.
- **Front-first gate.** Phase 2 is parallel-OK but 1I (front page) is the priority; Tab 2 reuses 1I's
  chart/surface/table components and BFF client seams — keep them clean, build 2D on top, don't fork.
- **uv only** for every backend command; **npm** for the web build/test. No bare `python`/`pip`.

## Not in this task (out of scope / later)

- **Automatic decorrelation optimiser** — any routine that searches, weights, or selects sub-strategies
  to minimise correlation / maximise diversification. 2D provides composition + aggregation only; an
  optimiser is a separate, later workstream and is explicitly excluded (guarded by
  `test_no_decorrelation_optimiser`).
- **Cross-currency dollar netting** — summing dollar Greeks across currencies into one number; stays
  line-level and currency-tagged per ADR 0006 / `risk/aggregation.py`.
- **Live/intraday book PnL** — 2D's PnL surface is the stressed (2B-grid) reprice of a dated book, not a
  streaming intraday mark.
