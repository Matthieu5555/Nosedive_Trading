# frontend-scenario-rate-axis-wiring — surface the rate-shock stress axis on the BFF + front

> **Front/BFF slice of [infra-scenario-rate-axis](infra-scenario-rate-axis.md).** The
> engine + config landed: `ScenarioConfig.rate_shocks`, a `rate` family in the scenario
> grid (separate parallel sweep, not a 3-D spot×vol×rate cross-product — owner-ruled), and
> the Rho attribution term now fires under a rate scenario. The parent left the **BFF/front
> wiring of the rate axis deferred / front-adjacent**. This is that wiring slice.

## Why (TARGET cite)
TARGET §5.4 (the risk-manager's stress screen: spot ±X%, vol ±X pts, **rates ±X bp**) and
§2 goal — the stress screen is how S2's kill condition and the book's rate exposure get
*seen*. The rate family is now produced by the engine but the stress-surface BFF
(`/api/risk/scenarios`, `/api/basket/scenarios`) and the Risk Scenarios / Basket-stress
front panels only render the spot×vol surface — the rate sweep reaches no screen. This
layer owns "carry it through to the operator's stress screen, labelled in bp and dollars".

## Scope boundary
- **In:** surface the `rate` scenario family through the stress-surface serializer and the
  `/api/risk/scenarios` + `/api/basket/scenarios` payloads (a labelled rate sweep beside
  the spot×vol surface); add the matching `api.ts` types; render it on the Risk Scenarios
  page and the Basket on-demand stress action (a rate-shock selector / row, each cell
  labelled with its bp shock and its dollar reprice delta). Reuse the existing
  `StressSurface` component idiom; honest empty state when `rate_shocks` is empty
  (backward-compatible — no rate family, no rate panel).
- **Out:** the scenario engine, the additive forward-fixed rate shock, the Rho term wiring,
  and the grid-shape ruling — all landed in the parent. Never re-shock or re-reprice in the
  BFF; serialize the engine's rate-family valuations. The full 3-D spot×vol×rate
  cross-product is explicitly **not** the shape (owner ruled a parallel sweep).

## Dependencies / coordination
- Reads the landed `rate` family from infra-scenario-rate-axis (engine + config on main).
- A rate-shock stress is only fully meaningful once Rho bumps a **real** curve
  ([infra-rates-curve-ingest](infra-rates-curve-ingest.md), R1) — note the dependency, but
  the additive forward-fixed sweep is renderable today against the parity-implied rate.
- Coordinate the Risk Scenarios / Basket page edits with the anthony lane (Basket/Risk tab
  operator-flow fixes) — shared-tree hazard on `pages/RiskScenarios.tsx` / `pages/Basket.tsx`.

## Done criteria
The rate-shock sweep is in the `/api/risk/scenarios` + `/api/basket/scenarios` payloads and
renders on the Risk Scenarios page + Basket stress action, each cell labelled in bp and
dollars; empty `rate_shocks` renders no rate panel (backward-compatible); no reprice
re-implemented in the BFF; Python BFF tests + web gate green.
