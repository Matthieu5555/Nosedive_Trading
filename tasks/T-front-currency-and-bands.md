# T-front-currency-and-bands — €-not-$ display + un-hardcode the band selector

**Status:** in progress. Backend single-source for the **currency** landed (2026-06-13);
front display wiring + the **band-axis source** remain. Page-1 design is **validated** — this
is fix/improve only, **no redesign** (owner).

Owner asks (2026-06-13): (1) « enlever le hardcode [des bandes] et faire en sorte que nos
nouvelles bandes s'affichent en scroll » ; (2) greeks monétisés en **€** pour le SX5E, pas `$`
(« idéalement dynamique au sous-jacent, mais on s'est concentré sur un seul pour l'éviter » →
drive it from the registry currency, trivially EUR here).

## Already done (backend single-source for currency)

`/api/indices` now serves `currency` per index straight from the registry `IndexEntry.currency`
(`routers/indices.py` + `tests/test_indices_api.py`, EUR for SX5E). The blueprint requires the
**correct currency** on monetized Greeks (05-math-notes: *"monetized Greeks use the correct
contract multiplier and currency"*), so this is not optional polish. The stored
`dollar_*_unit` strings still say `"$"` — a legacy contract artifact; the **front** renders the
real currency from the payload (no re-capture). A deeper fix (currency-aware stored unit string)
is a later contract change.

## (2) Currency — front wiring (remaining)

The data flow is traced and bounded:
- `web/src/api.ts` `IndexOption`: add `currency: string`.
- The pages already fetch `/api/indices` and know the selected underlying (`Basket.tsx`,
  `Market.tsx`/`market/IndexAnalytics.tsx`) → derive the selected index's `currency`.
- Thread `currency` into the monetized-value renderers: `components/DollarGreeks.tsx`
  (the `"$ value"` header + `formatDollar` → use `money(value, currency)` / the currency symbol),
  via `MaturityAccordion.tsx` and `charts.tsx`. `lib/format.ts` `money()`/`signedMoney()` already
  take a `currency` arg (default `"USD"` → pass the real one).
- The scenario-PnL unit (`serializers.py SCENARIO_PNL_UNIT = "$ (full-reprice PnL)"`) and the
  basket panel likewise render the underlying's currency (the basket already resolves it via
  `routers/basket.py:_option_multiplier_currency`).
- Tests: indices payload has currency (done); a component test that DollarGreeks renders `€` for
  an EUR underlying.

## (1) Un-hardcode the band selector

`components/BasketLegGrid.tsx:20` hard-codes the old 8-band list
(`["30dp","20dp","10dp","atm","atmp","10dc","20dc","30dc"]`). These 8 are a *subset* of the new
**32** (`T-delta-step-2`), so nothing crashes — but the selector hides 24 bands and violates the
"no hardcoded config lists" rule (the index picker is already registry-driven via `/api/indices`).

Fix: expose the **band axis** (the projection's `band_labels`, platform-wide, derived from
`qc_threshold.grid` via `ProjectionConfig.from_band`) from the backend and have `BasketLegGrid`
consume it (so all 32 appear; the table/scroll handles length). Source options:
- a small `/api/config/delta-bands` (or fold into an existing meta/config endpoint), returning the
  ordered band labels — the single source, same for every index; **or**
- include `delta_bands` once in the analytics meta the Basket page already loads.
The greeks **table is already data-driven** (`DollarGreeks` maps the received points, ordered
put→ATM→call) — it shows all 32 with scroll automatically once the data has them; **no change
needed there**.

## Constraints

Shared tree, active front lane (the `/api/indices` registry-selector work just landed). Stage by
explicit path; keep to the files above. No page-1 redesign — fix only.
