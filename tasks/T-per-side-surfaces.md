# T-per-side-surfaces (R2) — fit put/call/combined surfaces; carry surface_side; put−call spread QC

> **Source:** TARGET §4 ruling **R2** + §7.6 + §5.3; course transcript req #1 (IV per option,
> never mutualised). **Needs an ADR + blueprint amendment before build** (R2 changes the surface
> contract, ADR 0011).

## The gap
Today **one** surface is fitted per underlying per day (parity → one IV per strike). The course
insists the **put IV ≠ call IV** for the same (K, T) and wants IV calibrated per right.
[[T-mirror-greeks-putcall]] **explicitly scopes itself out of R2** — it keeps ONE IV curve and
mirrors only the *greeks* per side. **This is NOT that task:** R2 fits the surfaces per side.

## Scope
- Fit **three** surfaces per underlying/tenor: **put-side, call-side, and combined**; carry
  `surface_side ∈ {put, call, combined}` through the surface contract → projection → BFF → front
  (a side toggle on the 3D surface + smiles).
- The **put−call IV spread per (tenor, strike)** = a signal AND a QC instrument: a persistent
  spread = forward/dividend/borrow mis-estimate or a funding skew (tradable); a blowout = bad
  data quarantined before it reaches a strategy.
- The **combined** surface remains the forward-backing + attribution reference.

## Depends on / cross-link
Cross-link [[T-mirror-greeks-putcall]] with a "this is NOT R2" pointer on both. Per-name surfaces
feed [[T-signal-layer]]/S1.

## Done criteria
put/call/combined fitted; `surface_side` through contract→BFF→front with a toggle; put−call spread
QC + persisted signal; ADR + blueprint amendment landed; goldens regenerated; gate green.
