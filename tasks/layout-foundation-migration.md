# Layout foundation migration

The web app (`apps/frontend/web`) now has a deep-module layout foundation that OWNS spacing,
sizing, overflow and the numeric/maths font. This is the cure for the owner's bug list: random
spacing, elements stuck together, things overflowing, no real maths font, the ugly top error strip.

This doc tells coders exactly which primitives to use and gives a file-by-file task list. The
foundation and the Basket exemplar are already done (uncommitted, in the live checkout). Do NOT
re-touch them; copy the Basket pattern onto the remaining pages.

## The rule

Never type a raw `px` margin, gap, or width in a page again. Spacing comes from the scale; layout
comes from the primitives. If you reach for `margin`, `style={{ marginTop: ... }}`, or a new
`gap: 17px` CSS rule, you are doing it wrong. The call site must not be able to get spacing wrong.

## What exists (do not modify)

- `src/styles/foundation.css` - spacing tokens (`--space-*`), type scale, the bundled `@font-face`
  for `--font-numeric`, the layout-discipline baseline (auto overflow containment), and the
  `.l-stack/.l-cluster/.l-grid/.l-scroll` + `.error-modal__*` styles. Imported first by `index.css`.
- `src/assets/fonts/numeric-mono*.woff2` - the self-hosted DejaVu Sans Mono subset (digits, Greek
  δ Δ ρ̄ ν θ, operators, units). Already wired to `--font-numeric`.
- `src/components/layout/index.tsx` - the primitives: `Stack`, `Cluster`, `Grid`, `Scroll`.
- `src/components/ErrorModal.tsx` - the centered error modal that replaced the top strip.

## The primitives

```tsx
import { Stack, Cluster, Grid, Scroll } from "@/components/layout";
// (from a page file the path is "../components/layout" or "../../components/layout")
```

- `<Stack gap="md">` - vertical rhythm. ONE gap owns all spacing between children. Children carry
  no margins. Use it for a page body, a panel's inner content, a list of cards. Gaps:
  `none 3xs 2xs xs sm md lg xl 2xl 3xl` (4px base: xs=8, sm=12, md=16, lg=24, xl=32).
- `<Cluster gap="sm" align="end">` - a horizontal row that WRAPS instead of colliding. Use it for a
  row of controls (label+select), a button group, a control-row. `align`/`justify` optional.
- `<Grid min="240px" gap="md">` - responsive auto-fit columns, each `minmax(0,1fr)` so a cell can
  NEVER overflow. Use it for metric-card grids, equal-panel rows.
- `<Scroll label="...">` - contains a wide table or chart canvas AT THE SOURCE. Wrap every bare
  `<table>` and every chart that can exceed its column. The wide content scrolls inside this box;
  the page width stays bounded. This is the fix for horizontal-page-scroll bugs.

All take `as` (e.g. `as="section"`), `className` (composed, skin classes still work), and `role` /
`aria-*` / `data-*` pass-throughs.

## The exemplar: Basket (copy this)

`src/pages/Basket.tsx` and `src/pages/basket/*.tsx` show the full pattern:
- Page root is `<Stack as="section" className="page" gap="md">`.
- Each `.panel` wraps its inner content in `<Stack gap="md">` (heading, lede, controls, body) so the
  old `.basket-controls`/`.basket-templates`/`.basket-actions` margins are gone.
- Control rows became `<Cluster gap="sm" align="end">`; button groups `<Cluster gap="xs">`.
- The `.panel` / `.panel-heading` / `.panel-kicker` SKIN classes stay (they paint the surface). Only
  the SPACING wrappers were replaced.

Verify your page with the live-browser diagnostic, NOT jsdom:
`node scripts/diag.mjs <yourtag>` then read `scripts/diag-shots/<yourtag>/<page>-{wide,narrow}.png`
and check `over=0` for your page at narrow (390px). jsdom has no layout engine and cannot see this.

---

## Task list (file-disjoint, parallel-safe)

Each task is one page/concern. Shared-seam files (`index.css`, `App.tsx`, `main.tsx`,
`foundation.css`, `components/layout`) are OWNED by the foundation and must not be edited by these
tasks except where explicitly noted. If a task needs a new shared CSS rule, it almost certainly
should be a primitive instead - raise it, don't hand-roll.

### T1 - Market page + Greeks overflow + horizontal-scroll fix
Files: `src/pages/Market.tsx`, `src/pages/market/ConstituentsWorkspace.tsx`,
`src/components/TenorPanel.tsx`, `src/components/DollarGreeksByMaturity.tsx`,
`src/components/DispersionStrip.tsx`.
- Replace the `.market-scroll` flex column and any per-panel margins with `<Stack gap="md">`; wrap
  panel internals in `<Stack>`.
- VERIFIED OFFENDER (narrow 390px, document overflows ~128px): a non-wrapping `<p class="panel-note">`
  in the Greeks block and the Plotly `.modebar` painting outside the figure. Wrap the Greeks tables
  in `<Scroll>` (the existing `.greeks-by-maturity-scroll` already does this - confirm it covers the
  caption/notes too) and ensure the `panel-note` lede wraps (it inherits `overflow-wrap` only inside
  `.page p`; keep it inside the Stack).
- Acceptance: `node scripts/diag.mjs market` -> `market narrow over<=2`, `market wide over<=2`.
  Screenshot both viewports; the Greeks table scrolls inside its panel, the page does not scroll
  sideways.

### T2 - Signals page (horizontal-scroll fix)
Files: `src/pages/Signals.tsx`, `src/components/SignalsView.tsx`.
- VERIFIED OFFENDER (narrow, ~136px): bare `<table>` elements in SignalsView with no scroll wrapper.
  Wrap each table in `<Scroll>`. Replace page/section margins with `<Stack>`.
- Acceptance: `node scripts/diag.mjs signals` -> `signals narrow over<=2`.

### T3 - Strategy page
Files: `src/pages/Strategy.tsx`, `src/components/BacktestForm.tsx`,
`src/components/BacktestResults.tsx`, `src/components/EquityCurve.tsx`,
`src/components/GreeksOverTime.tsx`.
- Convert to `<Stack>`/`<Cluster>`; wrap any wide result table in `<Scroll>`.
- Acceptance: diag `over<=2` at both viewports; even vertical rhythm in screenshots.

### T4 - Risk Scenarios page
Files: `src/pages/RiskScenarios.tsx`, `src/components/StressSurface.tsx`,
`src/components/NamedScenarios.tsx`.
- Same migration. The `.risk-grid` can become `<Grid min="280px">`.
- Acceptance: diag `over<=2`; the scenario grid reflows at narrow without collisions.

### T5 - Positions page
Files: `src/pages/Positions.tsx`, `src/components/PositionsTable.tsx`,
`src/components/Reconciliation.tsx`.
- `<Stack>` for the page; wrap the positions/reconciliation tables in `<Scroll>`.
- Acceptance: diag `over<=2`; tables scroll inside their cards.

### T6 - Operations page (layout only; IBKR wiring is T7)
Files: `src/pages/Operations.tsx`, `src/components/operations/*.tsx`.
- The Card stack spacing -> `<Stack gap="md">`; control rows -> `<Cluster>`.
- Acceptance: diag `over<=2`.

### T7 - IBKR connection actually works (BFF + thin web touch)
Files: `apps/frontend/src/algotrading/frontend/routers/ibkr.py` (primary),
`src/components/operations/IbkrConnectionPanel.tsx` (copy-shell only).
- See "IBKR wiring" below. The gateway is LIVE and authenticated; the only bug is the env gate.

### T8 - Em-dash sweep (BFF rendered strings)
Files: `apps/frontend/src/algotrading/frontend/assistant_prompt.py`, `grounding.py`,
`serializers.py`, `routers/*.py`, `basket_scenarios.py`, `runner.py` - but ONLY string literals that
are serialized to the client or shape assistant output. Do NOT touch `#` comments or docstrings.
- Replace every em dash (U+2014) in a rendered string with a comma or " - " (hyphen with spaces),
  per the owner's no-em-dash directive. Also sweep any remaining em dashes in rendered web strings
  (`src/**/*.tsx` JSX text and string props, excluding `*.test.*` and code comments).
- Acceptance: `grep -rn $'—' apps/frontend/src/algotrading/frontend --include=*.py` returns only
  comment/docstring lines; `grep -rn $'—' src --include=*.tsx | grep -v test` returns only
  code-comment lines. Spot-check the assistant reply and an error toast in the live app.

### T9 - (optional) sweep residual raw-px spacing in shared component CSS
Files: `src/index.css` (SHARED SEAM - coordinate; ideally the foundation owner does this).
- Audit remaining `margin`/`gap` literals in component rules; fold the spacing-bearing ones onto
  `--space-*` tokens. Low priority; the primitives already win at the page level.

## IBKR wiring (T7) - findings and plan

VERIFIED LIVE on this host:
- The IBKR Client-Portal gateway is UP at `https://127.0.0.1:5000/v1/api` (self-signed TLS).
- `GET https://127.0.0.1:5000/v1/api/iserver/auth/status` ->
  `{"authenticated":true,"established":true,"connected":true,...}`.
- `GET .../iserver/accounts` -> `{"accounts":["DUQ574355"],...}` (the paper account).
- The BFF default base URL (`packages/infra-ibkr/.../session_factory.py::_GATEWAY_DEFAULT_BASE_URL`)
  is exactly `https://localhost:5000/v1/api`. So the transport already points at the live gateway.

THE BUG: `routers/ibkr.py` gates EVERYTHING on `gateway_requested()`, which reads the env var
`IBKR_CP_GATEWAY`. That var is UNSET in this environment, so `/status` short-circuits to
`configured:false` and `/connect` returns the 409 no-op the owner saw - even though the gateway is
live and authenticated.

THE FIX (probe reality, don't gate on a flag):
1. In `_status_payload()` and `ibkr_connect()`, when `gateway_requested()` is false, still attempt a
   cheap probe of the base URL (`session.authenticated()` wrapped in the existing
   `CpRestTransportError` handling). If the gateway answers, report the REAL state
   (`configured:true`, authenticated/established from the probe, account from `/iserver/accounts`).
   Only fall back to the `configured:false` not-configured payload when the probe cannot reach any
   gateway (connection error, not a 401). I.e. "configured" means "a gateway answers on the base
   URL", a fact, not "an env var is set".
2. `/connect` then works for free: authenticated -> `open_brokerage_session()` (ssodh/init), return
   the refreshed status. Never run the selenium login from the web (unchanged).
3. Keep it offline-safe: still never 500; a dead gateway still degrades to the labelled
   not-reachable status.
4. The web `IbkrConnectionPanel.tsx` needs no logic change (it already renders status + connect from
   the BFF). Only update the copy if a string still implies "not configured" when it is in fact live.
- Acceptance: with the live gateway up, `curl -s http://127.0.0.1:8000/api/ibkr/status` returns
  `configured:true, authenticated:true, established:true, account:"DUQ574355"`, and the Operations
  page shows "Session ready" with the account. Add/adjust `apps/frontend/tests/test_ibkr_router.py`
  to cover the probe-without-env path (mock the transport, do not hit a live gateway in the test).

## Shared-seam risks (call out before you start)

- `index.css`, `App.tsx`, `main.tsx`, `foundation.css`, `components/layout/*` are foundation-owned.
  Page tasks T1-T6 should not need to edit them. If two tasks both want a new shared CSS rule,
  it is a primitive - escalate.
- `src/api.ts` is touched by T7 only if a new field is added to `IbkrStatus`; coordinate with T8 if
  both edit it (T8 only changes string literals).
- Every page task is otherwise file-disjoint and parallel-safe in the shared checkout.
