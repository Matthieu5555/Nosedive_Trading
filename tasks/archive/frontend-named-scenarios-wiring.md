# frontend-named-scenarios-wiring — surface the named scenarios + correlation axis on the risk screen

> **⛔ BLOCKED — do NOT start (2026-06-16).** The **named** half **landed** (`frontend-risk-scenarios-rework`,
> see State below). All that remains is the **correlation axis**, which is **gated/dormant**: a ρ̄ bump
> reprices to zero on the live option book until a real `BasketCorrelationExposure` exists — and the
> owner ruling is **do not fabricate one**. So there is nothing pickable here until that exposure
> lands. Left open only to track that follow-up.

> **Deferred / owner-ruled (front-adjacent), exactly like the rate axis.** The compute side
> (engine + config) landed in [infra-named-scenarios-and-corr-shock](archive/infra-named-scenarios-and-corr-shock.md):
> `ScenarioConfig.named_scenarios` (seeded `2008` + `covid-2020`) and `ScenarioConfig.correlation_shocks`
> are built, hashed, and tested. The BFF/front wiring of the two new families is the remaining half.

## The gap
The §5.4 risk screen renders the parametric spot/vol/(rate) families. It does not yet surface:

- the **named historical scenarios** — each one labelled compound full-reprice scenario
  (`family="named"`, id `named_<label>`), already in `scenario_grid` when the catalogue is
  configured. The BFF reads the grid and reports; the front needs to show the named cells
  (a distinct row/section, labelled by `2008` / `covid-2020`) and their worst-case attribution.
- the **correlation-shock axis** — `family="correlation"`. It is **dormant** on the live option
  book (a ρ̄ bump reprices to zero through the option pricer; it needs a real ρ̄ exposure via
  the realized-vol ρ̄ signal layer — constituent **bars**, not option capture (ADR 0051) — downstream). Wire the BFF/front to *display* the
  axis when configured, but it stays empty-by-default until the ρ̄ exposure is real — do not
  fabricate a correlation exposure on the live book to make the cell non-zero.

## Scope
- BFF: expose the named + correlation families in the risk/stress payload the front reads,
  preserving the byte-identical-when-empty contract (an unconfigured grid renders exactly as today).
- Front: a named-scenarios section on the risk screen; the correlation axis gated on a real
  `BasketCorrelationExposure` (which the live book does not yet carry).
- Playwright: extend the e2e for the new section when the catalogue is configured.

## Depends on
The correlation half is only *meaningful* once a real ρ̄ exposure lands (the realized-vol ρ̄ signal
layer — constituent **bars** + the signal set, ADR 0051; **not** constituent option capture). The
named half stands alone and is the nearer-term piece.

## State (2026-06-16, `frontend-risk-scenarios-rework`)
**Named half landed.** The Risk Scenarios page (tab 3) was reworked to be meaningful on first
load. The BFF read is additive and consumes what already exists: `named_scenarios_to_list`
(serializers.py) buckets the `scenario_id` `named_<label>` rows the EOD cron already persists into
`scenario_results`, and `/api/risk/scenarios` now returns a `named` list (`n_named`) beside the
parametric `surface` — byte-identical-when-empty preserved (an unconfigured grid has no `named_`
rows → `named: []`). No new persisted table, no recompute. The web `NamedScenarios` component shows
them worst-loss-first with plain captions. The page also surfaces book-level P&L attribution
(`/api/attribution?level=book`) and broker reconciliation (`/api/reconciliation`).

**Still deferred (unchanged):** the **correlation axis** stays dormant — a ρ̄ bump reprices to zero
on the live option book until a real `BasketCorrelationExposure` lands; not fabricated. **On-demand
book-level stress** (a fresh full-reprice over an arbitrary grid for the *persisted book*, no cron)
has no backend endpoint — the Basket Builder's `POST /api/basket/scenarios` remains the only
interactive stress path (for a *composed* basket). Building a book-level on-demand stress endpoint
is the open follow-up if the persisted-portfolio path needs live re-stressing.
