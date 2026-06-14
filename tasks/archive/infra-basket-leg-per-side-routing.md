# infra-basket-leg-per-side-routing — route basket legs to their named vol surface (ADR 0048)

> **STATUS: LANDED 2026-06-14** (branch `infra-basket-leg-per-side-routing`). Implements the
> point-4 opt-in of ADR [0048](../../.agent/decisions/0048-per-side-vol-surfaces.md): "selecting a
> wing is an explicit opt-in (S1's straddle legs)". Full gate green (1934 passed, 12 skipped).

## The gap

R2 (ADR 0048) built the per-side surfaces and emits put / call / combined rows per cell, but
every pricing consumer filtered to `combined`, and `BasketLeg` had no way to name a wing. So the
mispricing R2 set out to fix was still live at the basket level: an S1 straddle was a call cell +
a put cell, both priced off the **combined** surface — the exact mutualisation per-side surfaces
exist to avoid. The data was produced; nothing consumed it. This task cashes it in for the summed
basket and the BFF live-reprice.

## Scope (done)

- **Contract.** `BasketLeg` gains `surface_side: str = "combined"` (validated against
  `SURFACE_SIDES`). Default → zero behaviour change; an unspecified leg reads combined, the
  forward-backing / attribution reference. It is **not** the option right (still fixed by the
  band's `…p` / `…c` suffix) — only which surface the cell's IV comes from.
- **Summed basket** (`infra/risk/multileg.py`). New shared indexer `index_rows_by_cell_and_side`
  (keyed by `(cell, surface_side)`, ambiguity tracked per `(cell, side)`) + `resolve_cell_side`.
  Each leg routes to its named side; a requested wing with no fitted curve is a labelled
  `surface_side_unavailable` gap — never a silent combined fall-back (that would re-mutualise the
  IV the wing selection exists to separate).
- **BFF live-reprice** (`apps/frontend/.../basket_scenarios.py`). Same shared indexer + resolver,
  so the on-demand stress surface and the summed Greeks agree on which wing each leg priced off.
- **Booking** (`execution/concretization.py`) deliberately **unchanged** — combined, per ADR 0048
  point 4: it solves the strike off the combined surface (§3) and marks off the real listed quote,
  so a booked fill is already side-correct without selecting a wing.

## Tests

- `test_multileg.py`: routing to the named wing; default leg reads combined even with wings
  present; a straddle prices each wing off its own surface (price = call wing + put wing, not
  2× combined); requested-wing-with-no-curve → `surface_side_unavailable` gap; provider ambiguity
  isolated per side. Oracles are hand-chosen per-side dollar Greeks.
- `test_contracts_validation.py`: `surface_side` defaults to combined; unknown value rejected with
  the offending value; put/call/combined accepted.
- `test_basket_scenarios.py`: a call-wing leg reprices off the call IV (independent Black-76 at
  the call vol, distinct from combined); requested wing with no curve → gap.
- Golden `contracts_plane_rows.json` regenerated through `--regenerate`: the only change is the
  additive `surface_side":"combined"` on the basket fixture's legs.

## Deliberately not done

- The web toggle that *sets* a leg's `surface_side` (and the put/call/combined surface view) stays
  in [frontend-per-side-surfaces-toggle](../frontend-per-side-surfaces-toggle.md). The BFF
  `BasketLegIn` does not yet carry the field, so the live UI books combined legs — the capability
  is in the contract + risk engine for the strategy layer (S1) to use now.
- Per-side SVI-param persistence — still deferred per ADR 0048 (no consumer).
