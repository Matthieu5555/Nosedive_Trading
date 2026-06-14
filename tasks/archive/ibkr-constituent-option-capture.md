# T-constituent-option-capture — widen capture scope to the top-N index constituents' option chains

> **Source:** TARGET §0 + §3 (S1) + §7.4. **"The single biggest new lane."** S1 (dispersion)
> hard-blocker and R3 (implied correlation) input.

## The gap
Today the CP-REST capture runs at the **index** level only (`enabled_indices()` →
`FiredIndex(IndexEntry)`); constituents have **OHLC bars only** (`ohlc-constituent-backfill`,
landed), not option chains. The frozen universe model ([[index-only-app-refactor]], TARGET §0)
is *one enabled index + its top-N constituents* — the constituents become option underlyings at
this phase, **registry-driven, never a hand-set list**.

## Scope
- Resolve the **point-in-time top-N by index weight** (1A membership + SSGA weights) for the
  enabled index — N from config (course: top-10, theory top-50).
- Widen the capture scope to those constituents' option chains, reusing the existing CP-REST
  close-capture lane + the delta-band / tenor-bracket selection. Conids resolve via the
  registry's `constituent_conids` pattern (already present for SAN1 etc.).
- The analytics engine is already underlying-generic (`IvPoint`/`SurfaceParameters`/`projection`
  key on `underlying`) — no engine change; this is a capture-scope + universe-resolution lane.

## Depends on / blocks
Blocks [[infra-signal-layer]] (implied correlation needs per-name surfaces) and the S1 dispersion
book. Pairs with the Basket UI picker expanding to index + constituents.

## Done criteria
A close run banks option chains + surfaces + Greeks for the index's top-N constituents on the
same grid as the index; top-N is point-in-time and config-driven; QC covers the new names; gate green.
