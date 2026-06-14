# 0048 — Per-side vol surfaces: fit puts and calls separately, carry `surface_side` through the grid

Status: accepted (2026-06-14). Implements TARGET §4 **R2** and §7 #6; the infra core of
[`infra-per-side-surfaces`](../../tasks/infra-per-side-surfaces.md). Course transcript req #1:
*IV per option, never mutualised — puts price puts, calls price calls.*

## Context

Until now one vol surface was fitted per underlying per day: `fit_slice` calibrated a single SVI
slice per maturity over **all** the day's solved `IvPoint`s, both rights mixed. Every projected
grid cell (`ProjectedOptionAnalytics`) — put-wing, call-wing, and the two ATM pillars — read its
IV from that one surface. So at a given strike the put IV and the call IV were forced equal. That
erases the put–call IV spread, which is exactly the quantity a vol book trades on: a persistent
spread is a forward/dividend/borrow mis-estimate or a funding skew (tradable), and a blowout is bad
data that must be quarantined before a strategy ever sees it. It also mis-prices an S1 dispersion
straddle, whose call leg and put leg should be priced off their own wings.

The right (`C`/`P`) is **not** lost upstream — it is carried on every `InstrumentKey` and through the
IV solver; it is only collapsed when the single combined slice is fit. So fitting per side is a
re-grouping of inputs, not a data-capture change.

## Decision

**Fit three surfaces per underlying per maturity — put-side, call-side, and combined — and carry
`surface_side ∈ {put, call, combined}` as a first-class dimension of the projected analytics grid.**

1. **Fit.** In the actor's surface step, the day's `IvPoint`s for a maturity are split by the
   right of their `InstrumentKey` (`option_right`) into put-only and call-only sets. `fit_slice`
   runs three times: over the put points (put surface), the call points (call surface), and **all**
   points (the combined surface). The combined fit is bit-for-bit the old fit — same inputs, same
   call — so `combined` *is* the legacy surface under a new name.

2. **Grid contract.** `ProjectedOptionAnalytics` gains `surface_side: str` (default `"combined"`),
   and the registry primary key becomes
   `(provider, snapshot_ts, underlying, tenor_label, delta_band, surface_side)`. There are now up
   to three rows per `(tenor, delta_band)` cell. The default keeps every pre-existing row and
   fixture valid and unchanged — a legacy single-surface row reads back as a `combined` row.

3. **Same strike, three IVs.** The cell's strike is solved **once** off the combined surface
   (today's exact delta→strike inversion), so combined strikes never move. The put and call rows
   reuse that strike `k` and read their IV from the put / call surface at `k`, then reprice the
   cell's option (the right is still the band's right) at that IV. This is what makes the
   **put−call IV spread well-defined per `(tenor, strike)`** — `iv(put-surface, K) − iv(call-surface, K)`
   — rather than comparing two different strikes. A side whose surface cannot price the cell
   (insufficient points at that maturity) yields a labelled gap for that `(cell, side)`, never a
   guess; the combined side, fit over the most points, is the most complete.

4. **Combined is the reference.** The combined surface remains the forward-backing surface and the
   P&L-attribution reference. Every downstream consumer that does not care about the wing — basket
   risk (`risk/multileg.py`), the BFF live-reprice (`basket_scenarios.py`), execution
   concretization, and the grid-coverage QC checks — **reads `surface_side == "combined"` by
   default**, so their behaviour is unchanged. Selecting a wing is an explicit opt-in (S1's straddle
   legs, the spread signal, the future front toggle).

5. **Put−call IV spread = signal + QC.** A pure derivation `put_call_iv_spread(cells)` pairs the
   put and call rows of each cell and reports `iv_spread = put_iv − call_iv`. A QC check
   `check_put_call_iv_spread` flags `|spread|` beyond a configured bound (a blowout → quarantine
   signal). The spread is **derived from the persisted per-side grid rows**, not stored in a second
   table: the put IV and call IV are already persisted in the grid, so a separate spread table would
   duplicate them. A dedicated persisted signal row, if wanted, belongs to the signal-layer lane
   (`infra-signal-layer`), built on this derivation.

## Scope of this change (and what is deferred)

In: the fit split, the grid contract + PK, the projection emission, the combined-default reads in
every grid consumer, the spread derivation + QC check, ADR, goldens regenerated, gate green.

Deferred, by the owner ruling on scope:
- **The front + BFF side toggle** (3D surface / smiles per side) — a `frontend-` follow-up
  (`frontend-per-side-surface-toggle`), mirroring how the second-order-greeks front work was split
  out of its infra lane.
- **Persisting per-side SVI parameters** (`SurfaceParameters`/`SurfaceGrid` per side). Those tables
  have no per-side consumer yet (the front toggle would be the first); keeping them combined-only
  avoids golden churn on tables nobody reads per-side today. The per-side *fit* still happens — it
  feeds the grid — it is just not persisted as raw SVI params yet.
- **A dedicated persisted spread signal table** — see point 5; folded into `infra-signal-layer`.

## Consequences

- The projected grid roughly triples in row count on the production path (three sides per priced
  cell). It is provider-partitioned and version-sub-partitioned as before; nothing about the
  storage layout changes beyond the wider key.
- `combined` rows are byte-identical to the old grid except for the added `surface_side` field, so
  the regenerated golden differs only by that field on existing rows plus the new put/call rows.
- S1 (and any wing-aware strategy) can now price its put leg off the put surface and its call leg
  off the call surface by selecting `surface_side`, instead of mutualising one IV.
