# frontend-attribution-view — surface the P&L attribution decomposition on the front (week, §7 #2)

> **The missing host.** The attribution *compute* landed — `infra/risk/attribution.py`
> (2C: Δ/Γ/Vega/Θ + residual on a scenario shock) and `infra-second-order-greeks` steps 1-2
> (Rho/Vanna/Volga terms + realized day-over-day). But there is **no BFF router and no web
> panel** that shows it: `app.py` wires 13 routers, none for attribution; grepping `web/src`
> for "attribution" finds only a Plotly logo flag. `infra-pnl-attribution` explicitly punts
> the render ("a Plotly attribution view (waterfall) are 1I's to render — this task owns the
> seam shape, not the render"), and **no `frontend-` task picked it up.** This is that task.

## Why (TARGET cite)
TARGET §2 #5 is an explicit end-of-week deliverable — "P&L decomposition, strategy-level and
portfolio-level … each term in **dollars**, residual measured against the full reprice".
§5.2 calls attribution *the differentiator* ("what enforces the strategy contracts"). §7 #2
ranks attribution-completion a week item. The numbers exist on the compute side and reach no
screen — the exact "did it reach the operator, correctly labelled, in dollars" gap.

**This is also the host [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md)
assumes** — that task says "render them … in the attribution view (Rho/Vanna/Volga beside
Δ/Γ/Vega/Θ)", but the view does not exist. Build the view here; that task adds its terms.

## Scope boundary
- **In:** an attribution **BFF router** (`apps/frontend/src/algotrading/frontend/routers/`)
  that projects the landed `ScenarioAttribution` seam to a JSON-primitive payload (per-term
  dollar contributions + residual + the tolerance verdict), plus its serializer in
  `serializers.py` and a typed client in `api.ts`; a **web waterfall panel** (Plotly per
  ADR 0030: Δ → Γ → Vega → Θ → Rho → Vanna → Volga → residual, each bar dollar-labelled with
  its unit string), self-labelled ("what am I looking at"), with an honest empty/degraded
  state when no attribution exists for the `(book/portfolio, date)`. Wired for both the
  portfolio/book level and the per-position drill (§5.2 "drillable per term").
- **Out:** the attribution math and the `ScenarioAttribution` shape (landed in
  `infra-pnl-attribution` + `infra-second-order-greeks`) — **never re-decompose or
  re-reprice in the BFF**; the router serializes the engine's output. Charm is a *display*
  Greek, not an attribution term (the dPnL eq stops at Volga) — it belongs on the Greek
  panels ([frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md)),
  not in this waterfall. The strategy-level grouping over a composed book is
  [strategy-composition](strategy-composition.md)'s drill target — this task delivers the
  view it drills into.

## Dependencies / coordination
- Reads the landed `ScenarioAttribution` seam (`infra/risk/attribution.py`, on main) and the
  Rho/Vanna/Volga terms from `infra-second-order-greeks` (compute steps 1-2, on main).
- **Sequence with [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md):**
  build the waterfall host here; it adds the Rho/Vanna/Volga *bars* once the serializer
  carries them. Coordinate the `serializers.py` / `api.ts` touch — shared-tree hazard with
  that task and the sig-fig / currency lanes; small magnitudes respect the sig-fig formatter
  ([frontend-sigfig-scientific-display](frontend-sigfig-scientific-display.md)).
- Where it mounts: a labelled panel on the Basket page (beside `BasketRiskPanel`/stress) for
  the composed position, and the drill target for the §5.8 book view — pick one home and
  label it; do not fork a second attribution surface.

## Test surface
Read `tasks/TESTING.md`. Independent oracles; expected values from a source other than the
code under test.
- **BFF projection — seam test.** A fixture `ScenarioAttribution` → the router returns the
  per-term dollar contributions + residual + verdict; a renamed contract field turns the
  assertion red (mirror `test_readback_api.py` discipline). The BFF re-decomposes nothing —
  assert the payload equals the engine's output for the same input.
- **Bad input / empty.** No attribution for the `(book, date)` → 200 with a labelled empty
  body, never a 500; a bad `trade_date` string → 400.
- **Web component (Vitest + RTL, MSW).** Given a populated payload the waterfall mounts and
  renders one labelled bar per term + the residual, each with its dollar unit string; given
  the empty payload it renders the empty state. Assert user-visible text/bars, not internal
  state. A fetch error renders through `AsyncBlock`, not a blank page.
- Gate green: root Python gate (`ruff && mypy && lint-imports && pytest`) **and** the web
  gate (`npm run lint && npm test`).

## Done criteria
`GET /api/attribution` (or equivalent) projects the landed `ScenarioAttribution` to a
dollar-labelled per-term payload + residual + verdict, re-decomposing nothing; a Plotly
waterfall panel renders the decomposition (Δ→…→residual) with unit strings and an honest
empty state, mounted on a labelled home and reachable as the §5.8 drill target; both gates
green. Week goal §2 #5 becomes demoable.

## Gotchas
- **No second decomposition home.** The BFF serializes `infra/risk/attribution.py`'s output;
  it never sums Greeks or reprices. A number not already on the seam does not belong here.
- **Dollars, labelled.** Every bar carries its unit string (§5.1/§2.5); small terms keep
  their sig-figs (coordinate with the sig-fig task). "+$42,000 of vega" reads; "+3,281" does
  not.
- **The residual is the honesty meter (§5.2)** — render it as its own bar against the full
  reprice, never hide it or fold it into another term.
- **Shared-tree.** `serializers.py` / `api.ts` overlap the 2nd-order-greeks + sig-fig +
  currency lanes — claim the file rows and serialize the edits. **uv** for Python, **npm**
  for the web.
