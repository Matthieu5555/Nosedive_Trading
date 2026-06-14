# frontend-second-order-greeks-panels — carry Vanna/Volga/Charm through to the front panels

> **Front slice (step 3) of [infra-second-order-greeks](archive/infra-second-order-greeks.md) (landed/archived 2026-06-14).**
> Steps 1-2 (compute) landed: Vanna/Volga/Charm in `black76` / `dollar_greeks` /
> `PricingResult` (raw + cash + unit strings), and attribution carrying Rho/Vanna/Volga +
> realized day-over-day. Step 3 was deferred and explicitly marked "owned elsewhere" to
> avoid colliding with the 3A ticket lane: carry the new Greeks/terms through
> `serializers.py → api.ts → front panels`. This is that step.

## Why (TARGET cite)
TARGET §5.1 (Greeks in natural units **and** dollars, including the second-order set) and
§5.2 (attribution drillable per term). Course transcript req #3/#8. The pricing layer now
**emits** these columns but the pricing serializer lists fields explicitly, so the new
contract columns are **inert** until the BFF serializer and `api.ts` contract surface them
and the front term-structure / attribution panels render them. Until then the compute is
banked but never reaches the operator's screen — the exact "did it reach the screen,
correctly labelled, in dollars" gap this layer owns.

## Scope boundary
- **In:** extend `apps/frontend/src/algotrading/frontend/serializers.py` to surface
  Vanna/Volga/Charm (raw + cash) with their `UNIT_STRINGS` / `charm_unit_string`; extend
  the `api.ts` typed contract to match; render them in the dollar-Greeks term-structure
  panels and the attribution view (Rho/Vanna/Volga terms beside the existing
  Δ/Γ/Vega/Θ). Each value self-labelled with its unit string; small magnitudes respect the
  sig-fig formatter (coordinate with `frontend-sigfig-scientific-display`).
- **Out:** the pricing/attribution math (landed) — never re-implement a Greek or a
  $-conversion in the BFF; the serializer reads the cash values the compute layer already
  produced. Charm is a **display** Greek, not an attribution term (the dPnL eq stops at
  Volga) — render it on the Greek panels, not in the attribution decomposition.

## Dependencies / coordination
- Reads the landed `PricingResult` columns + `ScenarioAttribution` seam from
  infra-second-order-greeks (compute steps 1-2, on main).
- **Sequence after the 3A ticket lane and `frontend-sigfig-scientific-display` merge** —
  the parent flagged the serializer/`api.ts` edit as a collision risk with the 3A lane; do
  it once both have landed. Coordinate the `format.ts` touch with
  `T-front-currency-and-bands` (vincent) and the sig-fig task — shared-tree hazard.

## Done criteria
Vanna/Volga/Charm (raw + cash, unit-tagged) reach the front Greek panels; Rho/Vanna/Volga
attribution terms render in the attribution view; small Greeks keep their sig-figs; no math
re-implemented in the BFF; web gate green and the Python BFF tests green.
