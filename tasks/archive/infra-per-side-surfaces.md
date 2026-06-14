# T-per-side-surfaces (R2) — fit put/call/combined surfaces; carry surface_side; put−call spread QC

> **STATUS: infra core LANDED 2026-06-14** (branch `infra-per-side-surfaces`, ADR
> [0048](../../.agent/decisions/0048-per-side-vol-surfaces.md)). Delivered: per-side fit
> (put/call/combined) in the actor; `surface_side` in the `ProjectedOptionAnalytics` grid PK
> (combined byte-identical to the legacy single surface); `put_call_iv_spread` signal +
> `check_put_call_iv_spread` QC; every combined-only consumer (basket risk, booking, grid QC,
> CDC view) filters to combined; goldens regenerated; full gate green (1924 passed).
> **The front/BFF `surface_side` toggle + per-side SVI-param persistence are the remaining
> half → [frontend-per-side-surfaces-toggle](../frontend-per-side-surfaces-toggle.md).**
>
> **Source:** TARGET §4 ruling **R2** + §7.6 + §5.3; course transcript req #1 (IV per option,
> never mutualised). The R2 contract change is recorded as ADR 0048 (the "blueprint amendment" in
> the original spec is moot — `documentation/` is the dead tree; TARGET §4 + ADR 0048 are the
> authority).

## The gap
Today **one** surface is fitted per underlying per day (parity → one IV per strike). The course
insists the **put IV ≠ call IV** for the same (K, T) and wants IV calibrated per right.
[[infra-mirror-greeks-putcall]] **explicitly scopes itself out of R2** — it keeps ONE IV curve and
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
Cross-link [[infra-mirror-greeks-putcall]] with a "this is NOT R2" pointer on both. Per-name surfaces
feed [[infra-signal-layer]]/S1.

## Done criteria
put/call/combined fitted; `surface_side` through contract→BFF→front with a toggle; put−call spread
QC + persisted signal; ADR + blueprint amendment landed; goldens regenerated; gate green.
