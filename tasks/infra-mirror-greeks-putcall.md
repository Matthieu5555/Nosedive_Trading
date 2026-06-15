# T-mirror-greeks-putcall — both-sides (put/call) greeks per grid cell

**Status:** done — branch `agent-a50a8b5449d08707c`, 2026-06-15.

Implementation: additive-nullable mirror fields on `ProjectedOptionAnalytics` (option a from
spec); `_build_cell` prices the opposite right at the same IV; BFF serializer emits
`price_mirror` + `mirror_metrics`; 14 unit tests (put-call parity oracle, ATM price parity,
delta sign, dollar scaling, serializer round-trip, look-ahead probe) + 1 BFF integration test;
contracts-plane golden regenerated; surfaces + contracts READMEs updated.
Gate: 2089 passed, 12 skipped (ruff/mypy/lint-imports/pytest green).

## Why

The prof's smile teaching (`ThomasHossen/MM_options_trading.md`, Bloc 2-3 — `documentation/` is
gone; `ThomasHossen/MM_options_trading.md` is the canonical course reference) wants the **greeks
shown per side** — the classic delta S-curve has **two branches**: call delta `1 → 0` and put delta
`0 → −1` ("le put est l'image décalée"). Today the projection emits **one option right per band
strike** (the OTM one: put on the put wing, call on the call wing, call at ATM), so each strike
carries only one side's greeks. The front cannot draw the put branch on a call-wing strike (a
deep-ITM put) or vice-versa.

**Scope boundary (settled, do NOT widen):**
- The **smile (IV) stays ONE curve.** Put-call parity → one IV per strike; the two "sides" are
  the two wings (`k<0` puts, `k>0` calls) of the single fitted surface, and the skew is its slope
  (the `3-1_quatre_smiles` figure is four *shapes*, not put-vs-call). This is **not** a request
  for two observed IV curves (that "case B" would mean an ingestion/fit refonte and contradicts
  the parity + `TARGET.md`).
- Only **delta / theta / rho** differ by side. **Gamma / vega are identical** call vs put at one
  strike (one curve) — do not duplicate them blindly.

## What (the "mirror")

At each solved (tenor, strike) cell, also compute the **opposite right's** greeks at the **same
fitted IV** (one extra `price_european` call, shared IV — cheap, additive, no ingestion/fit/
surface change). Surface them so the front can render both delta/theta/rho branches.

Open design choice for the spec author:
- (a) widen `ProjectedOptionAnalytics` with nullable `*_put` / `*_call` (or `*_opp`) greek fields
  (additive-nullable, older partitions still read), or
- (b) emit a paired cell per strike (right as a column) — heavier on row count, simpler schema.

Either way: shared IV, shared gamma/vega; delta/theta/rho carry both sides. Contract change is
additive (registry + golden regen by design, pre-capture). Front: delta/theta/rho greek cards
plot both branches; the smile keeps coloring the put wing vs call wing from the existing
`delta_band` suffix (no data change needed for that part).

## Acceptance (sketch)

- Independent oracle: at one strike, `Δcall − Δput ≈ e^{-qT}` (= DF here), `Γcall == Γput`,
  `νcall == νput`, `Θ`/`Ρ` differ by the known parity terms.
- Round-trip + golden; look-ahead clean; gate green.
- Front: the delta card shows the full S (both branches); the smile shows the two wings.

## Touches

`infra/contracts` (ProjectedOptionAnalytics + registry), `infra/surfaces/projection.py`
(`_build_cell` emits the opposite right), `infra/pricing` (reuse), the BFF serializers, and the
front greek cards. Disjoint from `T-delta-step-2` (which only changed the band axis).
