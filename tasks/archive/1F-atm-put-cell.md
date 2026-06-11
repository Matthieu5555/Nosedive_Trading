# 1F-followup — ATM-put cell: make the true two-leg ATM straddle composable

> **Follow-up to the archived WS 1F** (`tasks/archive/1F-analytics-projection.md`), opened by 2A
> (2026-06-07). Small, additive change to the analytics projection grid. The blueprint (ADR 0011)
> overrides on any domain question.

## Why

A **straddle** is a call **and** a put at the **same ATM strike** — its defining property is being
≈delta-neutral and at the max-gamma/vega point (an ATM option is ~50Δ). 2A's basket builder wants a
one-click "straddle" template, but the WS-1F analytics grid currently stores **only a single ATM
cell, priced as a call** (`projection.py`: the `atm` band has `target_delta=0.0`, and
`option_right = "P" if target_delta < 0 else "C"` → it resolves to a **call**). There is **no ATM
put cell**, so a genuine two-leg ATM straddle cannot be composed by summing grid cells:

- Using the ATM cell twice = two calls (delta ≈ +1.0) — not a straddle.
- Using the ±30Δ pair = exactly the strangle, and not ATM.

So 2A ships the straddle button as the honest interim **single ATM leg** ("½ straddle"). This task
removes that limitation by emitting the ATM **put** at the same ATM strike, so the straddle becomes
two real legs.

Expert review (web-researched, 2026-06-07) confirmed: the ±30Δ proxy is wrong (it *is* the
strangle); the correct fix is an explicit ATM-put cell. See the implementation note in
[`tasks/2A-basket-builder.md`](2A-basket-builder.md).

## What to do (ordered)

1. **Add an ATM-put band to the projection axis.** In
   `packages/infra/src/algotrading/infra/surfaces/projection.py`, add an ATM-put pillar beside the
   existing `atm` (call) pillar — same **strike** (the ATM-forward strike the delta-0 solve already
   finds), `option_right="P"`. Suggested band label `atmp` (keep `atm` as the call for
   backward-compat with 1F/1I/2A; **do not** repurpose `atm`). The put's strike is the call's
   strike (not re-solved from a delta target) — the straddle's whole point is the *same* strike, so
   reuse the solved ATM strike and price a put at it. Carry the full decimal + dollar Greeks (the
   put's `delta ≈ call_delta − 1`, same gamma/vega/|theta|), unit-tagged like every other cell.
2. **Keep it config-driven / additive.** The band axis is config (`ProjectionConfig`); add the ATM
   put without breaking the existing axis or the pinned tenor grid. A grid written before this lands
   simply has no `atmp` cell (additive — older partitions stay readable). No `.py` literals for any
   economic parameter (C7).
3. **Flip the 2A straddle template to two legs.** Once `atmp` is produced, change the single
   `straddle` branch in `apps/frontend/web/src/basketTemplates.ts` from `[long atm]` to
   `[long atm (call), long atmp (put)]`, and update its test + label (drop the "½" caveat). Nothing
   else in 2A changes (the contract, the summation math, and the BFF seam are untouched).

## Test surface

Read [TESTING.md](TESTING.md). Specific cases:
- `test_atm_put_cell_shares_the_atm_call_strike` — the `atmp` cell's `strike` equals the `atm`
  cell's `strike` (same ATM-forward strike; independent-oracle: solve the ATM strike by hand from
  the forward and assert both cells carry it).
- `test_atm_put_greeks_are_a_put_at_the_atm_strike` — put `delta ≈ call_delta − 1` within tol;
  `gamma`/`vega` ≈ the ATM call's (same strike); priced by the same engine (cross-check vs
  `py_vollib`/QuantLib, never the code under test).
- `test_atm_straddle_is_delta_neutral` — `dollar_delta(atm) + dollar_delta(atmp) ≈ 0` (the
  straddle's defining property), `gamma`/`vega` ≈ 2× the ATM call.
- Web: `test_straddle_template_composes_two_atm_legs` — the straddle template builds
  `[long atm, long atmp]`, and straddle ≠ strangle still holds.
- Determinism / no-look-ahead unchanged (the ATM put is computed off the same snapshot as the call).
- Both gates green (root Python gate + web `npm run lint && npm test`).

## Done criteria

The projection emits an ATM-put cell at the ATM-call strike with full decimal + dollar Greeks; the
2A straddle template composes two genuine ATM legs (delta-neutral, 2× gamma/vega), proven by an
independent-oracle test; straddle ≠ strangle; both gates green.

## Gotchas

- **Same strike, not same delta.** The ATM put is at the ATM **call's strike**, not re-solved from a
  put delta target — a straddle is two legs at one strike. Reuse the solved ATM strike.
- **Additive only.** Keep `atm` as the call; add `atmp`. Don't break 1F/1I/2A which already read
  `atm`. Older partitions without `atmp` must still read back (additive-nullable discipline).
- **One pricing home.** Price the ATM put through the same engine as every other cell
  (`risk/greeks.py` / `pricing/`); do not hand-roll a put formula here.
- **Cross-refs:** archived [1F](archive/1F-analytics-projection.md), [2A](2A-basket-builder.md)
  (the straddle template + implementation note), P0.1 tenor grid / P0.2 dollar units.
