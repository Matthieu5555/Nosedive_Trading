# T-second-order-greeks — emit Vanna/Volga/Charm (raw + cash), then complete attribution

> **Source:** TARGET §7.2 + §5.1/§5.2; course transcript req #3 + #8
> (`documentation/transcripts/AlgoTradingCourse2-Greeks-et-strategies-vol.md`).
> **Highest-leverage gap:** it unblocks BOTH attribution completion and the course's
> 2nd-order requirement. Pricing emission is the prerequisite — attribution cannot add terms
> the pricer does not produce.

## The gap
`PricingResult` (`packages/infra/src/algotrading/infra/contracts/tables.py`) emits
delta/gamma/vega/theta/rho only — no vanna/volga/charm anywhere in `infra/src`.
`risk/attribution.py` (2C, landed) decomposes Δ/Γ/Vega/Θ + residual on a **scenario shock** only.

## Scope
1. Pricing: add **Vanna, Volga, Charm** (and the obvious 2nd-order set) to the Black-76 engine
   output, in **raw decimal AND cash** (€/$ per underlying, ADR 0036 monetization), with unit
   strings — same dual representation as the 1st-order Greeks.
2. Attribution: extend `risk/attribution.py` with **Rho, Vanna, Volga** terms and
   **realized day-over-day** dPnL attribution (today it's scenario-shock only). Residual stays
   measured against the full reprice (the honesty meter).
3. Carry the new Greeks through the contract → projection → BFF → front term-structure panels.

## Depends on / blocks
Rho term pairs with [[T-rates-curve-ingest]] (R1) for the curve it bumps. Blocks the §7.2
"attribution completion" and the transcript's "résidu en 2e ordre".

## Done criteria
Vanna/Volga/Charm in `PricingResult` (raw+cash, units); attribution carries Rho/Vanna/Volga +
realized dPnL; residual shrinks on the golden case; tolerances + look-ahead clean; gate green.

## Landed — steps 1 & 2 (compute only; step 3 deferred)
**Pricing.** Closed-form generalized-BSM Vanna `−e^{(b−r)T}φ(d1)·d2/σ`, Volga `vega·d1d2/σ`,
Charm `∂Δ/∂t` in `pricing/black76.py` (degenerate regime → 0; American/FD leave them 0.0
*explicitly*, a documented gap). Cash layer in `pricing/dollar_greeks.py`: `$vanna = vanna·S·0.01`
(Δ$ per 1 vol pt), `$volga = volga·0.01²` (Vega$ per 1 vol pt), `$charm = charm·S/day_count`
(Δ$ per day, rides the theta 365/252 fork). Unit strings in `UNIT_STRINGS`. `PricingResult`
carries all six (raw+cash) as additive-nullable; `engine.pricing_result` fills them through the
one canonical monetization home. FD-cross-checked (call+put, dividend cases).

**Attribution.** `TaylorTerms` + `terms_from_move` (one arithmetic home, move = dS,dσ,dt,dr) gain
`rho_pnl/vanna_pnl/volga_pnl`; the scenario grid holds rates fixed (rho term 0 there). New
`attribute_realized_line/_book` (+ `RealizedMove`) decompose **realized day-over-day** dPnL from
*start-of-day* Greeks × realized moves, residual vs the full reprice `price(t)−price(t-1)` —
look-ahead clean. `ScenarioAttribution` seam carries the three new terms (additive-nullable).
Charm is a display Greek, **not** an attribution term (TARGET dPnL eq stops at Volga).

**Step 3 (NOT done — deferred, owned elsewhere):** carry the new Greeks/terms through
`serializers.py → api.ts → front panels`. The pricing serializer lists fields explicitly, so the
new contract columns are inert until that lane wires them with `charm_unit_string` + `UNIT_STRINGS`.
Untouched here to avoid collision with the 3A ticket lane.
