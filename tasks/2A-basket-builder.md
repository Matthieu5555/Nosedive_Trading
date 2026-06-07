# 2A — Basket builder: compose a multi-leg position, priced & risked off Tab-1 analytics

> **Phase 2, Tab 2. Parallel-OK behind the front-first gate (target Fri 2026-06-12).** The front
> page (`tasks/1I-front-page.md`) is the priority; Phase 2 may proceed in parallel but does not
> jump the queue. 2A is the **position model + composition UI** — it does not build a new compute
> engine. The risk/scenario engine already exists (`infra/risk`, ADR 0006); 2A defines the typed
> multi-leg contract and the UI that feeds it, and is what 2B/2C/2D operate on. The blueprint
> (ADR 0011) overrides this spec on every domain question — the leg taxonomy, the $-Greek
> conventions, and what "priced from analytics" means are its calls; where this file and the
> blueprint disagree, the blueprint wins.

- **Owns:** a new **typed multi-leg position/basket contract** — the legs (each an option-or-stock
  reference + signed quantity + side), the basket envelope, and its serialization — added to
  `packages/infra/src/algotrading/infra/contracts/tables.py` (+ registry/serialization round-trip
  in `contracts/registry.py`); a pure **basket-pricing/risk function** under
  `packages/infra/src/algotrading/infra/risk/` that prices and risks a basket by **summing the
  per-position dollar Greeks already produced by 1F** (book-additive — no recompute); a **BFF
  basket router** on the Python side of `apps/frontend/src/algotrading/frontend/` (+ its serializer
  and registration in `app.py`); and the **basket-construction UI** on the web side
  (`apps/frontend/web/src/`) — a leg-entry grid and a live basket-risk panel. Conforms to
  **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)** (blueprint governs the
  domain), **[ADR 0006](../.agent/decisions/0006-risk-engine.md)** (the existing risk engine 2A
  feeds), **[ADR 0029](../.agent/decisions/0029-contract-field-names-conform-to-blueprint.md)** (the
  `dollar_*`/`forward_price`/`implied_vol`/`log_moneyness` field names), and
  **[ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)** (shadcn/ui
  + TanStack Table for leg entry, Plotly only where a chart helps).
- **Depends on:** **1F** (`tasks/1F-analytics-projection.md`) for the `ProjectedOptionAnalytics`
  grid + the per-position dollar Greeks each leg is priced from — without it there is nothing to
  price *against*; **P0.2** (`tasks/P0-contracts-and-unblockers.md` Task 2 → ADR 0035) for the
  complete `$`-unit contract (`dollar_theta`/`dollar_rho` on `PricingResult`, the unit strings, and
  the per-contract→per-position→**book-additive** rule that makes summation legitimate). The risk
  engine (`infra/risk` aggregation + scenarios + full-reprice, ADR 0006) and the real `/api/risk` +
  `/api/risk/scenarios` BFF endpoints already exist; 2A adds the position/basket contract those were
  always meant to consume. 1I delivers the web stack (Plotly + shadcn/ui + TanStack Table) and the
  `getJson`/`useFetch`/`AsyncBlock` patterns 2A reuses.
- **Blocks:** **2B** (stress/scenario — wires the ±50%/±50% grid to *the basket*), **2C** (PnL
  attribution — decomposes *the basket's* PnL by Greek), **2D** (strategy composition — layers
  *baskets* into a book). All three operate on the contract 2A freezes; build it clean so they are
  wiring, not rework.
- **State going in (verified 2026-06-07):** **no multi-leg position/basket contract exists yet** —
  the roadmap line "front + a position model" is aspirational; the model is this task. What *does*
  exist: a working in-memory `Position` (signed `Decimal` quantity, `contract_key`, `tags`) and a
  `PositionSet` in `infra/risk/positions.py`; a frozen seam `Position` (`valuation_ts`,
  `portfolio_id`, `contract_key`, `quantity`, `source`) in `contracts/tables.py`; order-free
  line→aggregate summation in `infra/risk/aggregation.py` (sum of lines == aggregate, reorder-
  invariant); `valuation.py`/`scenarios.py`/`greeks.py` for the compute. **`PricingResult` carries
  `dollar_delta`/`dollar_gamma`/`dollar_vega` only** — `dollar_theta`/`dollar_rho` arrive via
  1F/P0.2; do not assume them present until that lands. **Name collision to avoid:**
  `infra/risk/basket.py` already exists and is the *index-variance identity* (Eq 23, weights ×
  vols × correlation) — it is **not** a multi-leg position basket; do not overload it, name the new
  module distinctly (e.g. `risk/multileg.py` / `risk/legs.py`). The web app has the
  `getJson`/`useFetch`/`AsyncBlock` helpers; the Plotly/shadcn/TanStack deps land with 1I (this
  task consumes them, does not re-add them). `store_serving.py` and `/api/market` were **deleted in
  C4** — do not cite or resurrect them; the BFF reads the store read-only.

## Objective

An operator composes a **multi-leg position** — pick stocks and options into a basket (an ATM
straddle, a strangle, a risk-reversal, a custom set of legs) — and immediately sees that basket
**priced and risked**, where every basket number is the **sum of the per-position dollar Greeks 1F
already produced**, never a fresh pricing pass. The typed contract is the deliverable that makes
this real: a basket is an ordered set of **legs**, each leg a reference to one instrument
(option *or* stock, by its canonical `contract_key`) plus a **signed quantity** and an explicit
**side** (long/short), and the basket's price and Greeks are defined as the book-additive sum over
its legs of the dollar Greeks carried on the matching `ProjectedOptionAnalytics`
rows (option legs) and the spot exposure (stock legs). "Priced from Tab-1 analytics" is the hard
acceptance: the basket-risk panel reads the same grid Tab 1 renders and **sums**, so a leg's
contribution to basket Delta\$ equals that leg's `dollar_delta` from the analytics row times its
signed quantity — provably the same number, not a parallel computation. The contract is frozen and
rich enough that 2B drives a stress grid over it, 2C attributes its PnL, and 2D layers baskets into
a book — all without reworking 2A.

## What to do (ordered)

1. **Define the typed multi-leg contract.** Add a frozen, slotted **`BasketLeg`** and **`Basket`**
   (or the blueprint's names if it dictates them) to `contracts/tables.py`, same shape discipline as
   the other contracts. A `BasketLeg` carries at minimum: the leg's `contract_key` (the canonical
   key that joins it to the 1F analytics row and the universe), an `instrument_kind`
   (`option`/`stock` — the blueprint's taxonomy governs), a **signed quantity**, and an explicit
   **side** label, with the sign/side consistency enforced in `__post_init__` (a "long" leg with a
   negative quantity is a malformed contract, rejected with a structured error — not silently
   normalized). A `Basket` carries its ordered legs, an identifier, the `as_of`/`trade_date` the
   pricing is resolved against (the look-ahead anchor), and a provenance hook. Register both in
   `contracts/registry.py` via the **additive** schema-evolution path with a serialization
   round-trip, so the basket joins the rest of the store cleanly. Reuse, do not fork, the existing
   `infra/risk/positions.py` `Position`/`PositionSet` working model where a basket reduces to a set
   of signed positions — the basket is the *composed, named, side-aware* envelope over those, not a
   second position type.

2. **Build the pure basket-pricing/risk function.** Add a new module under `infra/risk/` (named
   distinctly from the existing `basket.py` index-variance file — e.g. `risk/multileg.py`) with a
   pure function `(Basket, the matching ProjectedOptionAnalytics rows + spot for stock legs,
   config) → a typed basket-risk result`. It resolves each leg's analytics row by `contract_key`
   (+ the basket's `as_of`/tenor), and computes the basket's price and **dollar** Greeks as the
   **book-additive sum** over legs of `signed_quantity · row.dollar_<greek>` (option legs) and the
   linear spot delta for stock legs — reusing the summation discipline already in
   `infra/risk/aggregation.py` (order-free, sum-of-lines == aggregate) rather than a new reduction.
   It is **not** a reprice: it reads 1F's dollar Greeks and sums them. Preserve the **line-level**
   per-leg contribution beside the aggregate (2C attributes off it, and it is what proves the
   "equals the 1F number" claim). A leg whose `contract_key` has **no** matching analytics row is a
   **labeled gap** (a structured diagnostic carrying the missing key), never a silent zero and never
   a bare NaN — 2B/2C and the UI surface it. Read the gamma-1%/theta-365 conventions and any
   multiplier handling from validated config (the C7 DI pattern), not `.py` literals; do not
   recompute the dollar Greeks here — that math has one home (1F / `risk/greeks.py`).

3. **BFF basket router (read + compose).** Add `routers/basket.py` on the Python side of
   `apps/frontend/`, registered in `app.py` alongside the existing routers (`health`, `surfaces`,
   `risk`, `run`, `config`, `oauth`). It accepts a basket composition (the legs the operator built)
   and returns the priced/risked basket — reading the 1F `ProjectedOptionAnalytics` table back
   through `ParquetStore` (**read-only**; the cron is the sole writer) and calling the step-2
   function. The serializer carries each dollar number with its **P0.2 unit string** (Delta\$ per
   \$1, Gamma\$ per 1% move, Vega\$ per vol point, Theta\$ per calendar day, Rho\$ per 1% rate) and
   uses the ADR-0029 `dollar_*`/`forward_price`/`implied_vol`/`log_moneyness` names — the units come
   from 1F/P0.2, not invented here. A leg referencing an unpriced contract → a **labeled gap** in
   the payload with HTTP 200, never a 500; a malformed basket → a labeled 400 (mirror the surfaces
   router's `bad_trade_date`). The HTTP shape is the seam — keep it in lockstep with the web client.

4. **Leg-entry UI (web).** On the web side, add a basket-construction view: a **TanStack Table**
   leg-entry grid (per ADR 0030) where the operator adds legs — pick an instrument (option from the
   Tab-1 tenor×delta-band grid, or a stock), set side and quantity — with a small set of
   one-click **templates** for the common multi-leg shapes the roadmap names (straddle, strangle,
   risk-reversal, …) that pre-fill the legs. Use shadcn/ui for the shell; reuse the
   `getJson`/`useFetch`/`AsyncBlock` helpers and the typed-client pattern in `api.ts` (add a
   `Basket`/`BasketLeg`/`BasketRiskResponse` interface mirroring the serializer — the HTTP shape is
   the seam). Validate leg entry user-side (side↔sign, non-zero quantity) and render the BFF's
   labeled gaps inline, never a blank panel.

5. **Live basket-risk panel (web).** Beside the leg grid, render the composed basket's **dollar
   Greeks** (each with its unit string visible) and price — the book-additive total — updating as
   legs change, plus the **per-leg contribution** breakdown (this is the visible proof that the
   basket number is the sum of the Tab-1 per-position dollar Greeks). Plotly only where a chart
   genuinely helps (e.g. a per-leg Greek-contribution bar); a table is fine for the totals. Every
   panel self-labels ("what am I looking at?"). A fetch error renders through `AsyncBlock`, not a
   blank page. **Do not** build the ±50%/±50% PnL surface here — that is 2B
   (`tasks/2B-stress-scenario.md` when it opens); 2A stops at the composed, priced, risked basket.

6. **Document the contract.** Add the basket contract + the "priced-by-summation, not recompute"
   rule to the blueprint-conformant docs (the data dictionary entry for the new contract, the
   book-additive invariant). Note the `infra/risk/basket.py` (index variance) vs the new multi-leg
   module distinction so the next agent does not conflate them.

## Test surface

Read [TESTING.md](TESTING.md). The independent-oracle, seam, reordering-invariance, edge-case, and
"name the case or it is not tested" rules there are mandatory. The cases specific to 2A:

- `test_basket_dollar_greeks_equal_sum_of_leg_analytics` — **independent oracle**: a 2–3-leg basket
  fixture whose per-leg dollar Greeks are hand-written in the test comment (chosen, not read from
  the code under test); the basket Delta\$/Gamma\$/Vega\$/Theta\$/Rho\$ each equal the hand sum
  `Σ signed_quantity · leg.dollar_<greek>` within float tolerance. This is the "priced from Tab-1
  analytics, not a recompute" claim made falsifiable.
- `test_basket_risk_is_reordering_invariant` — shuffling the legs leaves the basket aggregate
  identical (the order-free-summation invariant 2A inherits from `aggregation.py`).
- `test_straddle_template_composes_expected_legs` — the straddle/strangle/risk-reversal templates
  produce exactly the expected legs (count, sides, signed quantities), checked against the
  hand-listed legs in the test, not against the template code.
- `test_leg_side_sign_consistency_rejected` — a leg whose side contradicts its quantity sign is
  rejected with a structured error at contract construction, not silently normalized; a zero
  quantity is rejected.
- `test_unpriced_leg_is_labeled_gap_not_zero` — a leg whose `contract_key` has no matching
  `ProjectedOptionAnalytics` row produces a **labeled gap** carrying the missing key, never a silent
  zero, never a bare NaN; the basket aggregate reports the gap rather than absorbing it.
- `test_basket_contract_round_trips` (C→A seam, extend the contract-test home): `BasketLeg`/`Basket`
  serialize/deserialize equal and validate against the registry schema; at least one **malformed**
  instance is rejected by write-ahead validation with an explicit error, not a silent coercion.
- `test_basket_router_reads_back_and_sums` (BFF↔infra seam — extend
  `apps/frontend/tests/test_readback_api.py`, the pinned readback test): seed real
  `ProjectedOptionAnalytics` rows through `ParquetStore.write` (hand-chosen, internally consistent,
  derived independently of BFF output), POST/GET a basket over them, and assert the payload's basket
  Greeks equal the sum of those seeded rows' dollar Greeks with provenance intact.
- `test_basket_payload_uses_blueprint_field_names` — the payload uses the ADR-0029
  `dollar_*`/`forward_price`/`implied_vol`/`log_moneyness` names; a renamed contract field turns the
  assertion red.
- `test_basket_dollar_greeks_carry_unit_strings` (P0.2) — every dollar number in the payload carries
  a non-empty unit string with the pinned semantics; the decimal per-unit context is present beside
  the dollar number.
- `test_unpriced_leg_is_200_not_500` / `test_malformed_basket_is_400` — a leg on an unpriced
  contract returns a labeled gap with HTTP 200; a malformed basket returns a labeled 400 (mirror the
  surfaces router).
- **No look-ahead:** the basket prices off the analytics for its own `as_of`/`trade_date`; a later
  snapshot does not change the priced basket (run the `check-lookahead-bias` skill over the leg→
  analytics resolution join).
- **Web component tests** (Vitest + Testing Library, alongside `Surfaces.test.tsx`/`Risk.test.tsx`):
  the leg-entry grid renders and accepts a leg; a template button pre-fills the expected legs; the
  basket-risk panel shows the totals **with unit strings visible** and the per-leg contributions;
  every panel renders its self-label; a fetch error renders through `AsyncBlock`, not a blank page.
  Assert user-facing text/labels per the write-tests UI rule; mock the BFF, do not hit a live one.
- **Edge cases (the floor):** empty basket (no legs → labeled empty, not a crash), a single leg, a
  duplicate `contract_key` across two legs (both contribute; the sum is correct), a leg quantity
  exactly at a boundary, NaN/inf quantity rejected with a structured diagnostic.
- Branch coverage on the new pure basket module at or above the committed floor (≥90%).
- Gate green both sides: root Python gate (`uv run ruff … && uv run mypy … && uv run lint-imports
  && uv run pytest`) and the web gate (`npm run lint && npm test` in `apps/frontend/web`).

## Done criteria

A typed multi-leg position/basket contract (`BasketLeg`/`Basket`) exists, frozen, registered, and
round-tripping through the store via the additive path; a pure `infra/risk` function prices and risks
a basket as the **book-additive sum of the per-position dollar Greeks 1F produced** — proven equal to
those numbers by an independent-oracle test, never a recompute; a BFF basket router composes/reads
the basket read-only from `ParquetStore`, with ADR-0029 field names and P0.2 unit strings, returning
labeled gaps (200) and labeled errors (400) rather than 500s; the web basket-builder lets an operator
add legs (TanStack Table) and one-click templates (straddle/strangle/…), and renders the live
book-additive basket Greeks with units plus the per-leg contribution breakdown; no look-ahead; both
gates green. The contract is rich enough that 2B/2C/2D are wiring, not rework.

## Gotchas

- **"Priced from Tab-1 analytics" means summation, not a second pricing pass.** The whole point of
  2A is that a basket number is the book-additive sum of the per-position dollar Greeks 1F already
  computed and stored. If you find yourself calling the Black-76 engine here, you have taken the
  wrong path — read the `ProjectedOptionAnalytics` rows and sum. The test that pins this is
  `test_basket_dollar_greeks_equal_sum_of_leg_analytics`.
- **`infra/risk/basket.py` is NOT this.** It is the index-variance identity (Eq 23, weights × vols ×
  correlation). The multi-leg position basket is a *new* module — name it distinctly and do not
  overload the existing file or its `BasketVarianceResult`.
- **Book-additivity is a P0.2 precondition.** Summing dollar Greeks across legs is only legitimate
  because P0.2/ADR 0035 pins the per-contract→per-position→book-additive rule and the units. Until
  P0.2 lands, `PricingResult` has no `dollar_theta`/`dollar_rho`; do not assume them present, and do
  not invent a parallel dollar-Greek code path to fill the gap — sequence after 1F/P0.2.
- **Blueprint (ADR 0011) overrides** the leg taxonomy, the side/sign convention, the $-conventions,
  and the data-dictionary names. Follow it where it differs from this file and note the divergence —
  do not encode this file's defaults over it.
- **Front-first gate.** 1I (`tasks/1I-front-page.md`) is the priority; Phase 2 is parallel-OK but
  does not jump it. Reuse 1I's web stack and BFF patterns — do not re-add Plotly/shadcn/TanStack or
  fork the `getJson`/`AsyncBlock` helpers.
- **Read-only serving; the cron is the sole writer (ADR 0034 §1 / 0033).** The basket router opens
  the store read-only. Composition is an in-request computation over read-back analytics, not a
  write. Do not resurrect `store_serving.py` or `/api/market` (deleted in C4).
- **The HTTP shape is the seam.** A serializer change without the matching `api.ts` change is silent
  drift; the readback seam test keeps it honest.
- **Cross-refs:** 1F (`tasks/1F-analytics-projection.md`), P0.2 (`tasks/P0-contracts-and-unblockers.md`),
  1I (`tasks/1I-front-page.md`), and downstream 2B/2C/2D (specified when they open).
- **uv** for the Python contract/risk/BFF work; **npm** for the web app. No bare `python`/`pip`; one
  charting dependency (Plotly), no second.

## Implementation note — template realization on the delta-band grid (2026-06-07)

The one-click templates (`apps/frontend/web/src/basketTemplates.ts`) are realized on the course's
three pillars (−30Δ put / ATM / +30Δ call). Decided with web-research expert review:

- **Strangle** = long +30Δ call + long −30Δ put (the two OTM wing cells). Maps cleanly.
- **Risk reversal** = long +30Δ call, short −30Δ put. Maps cleanly.
- **Straddle** = a call **and** a put at the **same ATM strike** (defining property: ~delta-neutral,
  max gamma/vega; an ATM option is ~50Δ, so the ±30Δ wings are *not* a straddle — that pair *is* the
  strangle). **Resolved (1F-followup landed):** the analytics grid now emits **both** ATM pillars at
  the one ATM-forward strike — the call `atm` and the put `atmp` (`tasks/1F-atm-put-cell.md`;
  `projection.py` `_option_right_for_band` takes the right from the label suffix). So the straddle
  template composes the genuine **two ATM legs** `[long atm, long atmp]` — delta-neutral, 2× gamma/
  vega. It is deliberately **not** the ±30Δ pair (that is the strangle; a straddle and a strangle
  must not compose identical legs).

This is a UI-only convenience layer over the frozen `Basket`/`BasketLeg` contract; changing a
template is a one-file edit and does not touch the contract, the summation math, or the BFF seam.
