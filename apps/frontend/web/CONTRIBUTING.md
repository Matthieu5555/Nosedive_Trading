# Contributing to the operator console (`apps/frontend/web`)

This is the contributor playbook for the React/Vite web app. It exists so that many people can
commit here without quietly breaking the build, the layout, or the BFF contract. The rules below
are mostly **machine-enforced** (ESLint, Prettier, tsc, Vitest, Playwright) — this file explains
the *why* and the *where*, and is what the layer-boundary error message points you to.

For what the app *is* (the BFF, the pages, the API surface), read
[`../README.md`](../README.md) first — this file does not repeat it.

## Fast path

```bash
cd apps/frontend/web
npm ci          # clean, lockfile-exact install
npm run dev     # Vite on 127.0.0.1:5173, /api proxied to the BFF on :8000
```

Before you push, run the full local gate (the same checks CI runs):

```bash
npm run format:check && npm run lint && npm run typecheck && npm test
npm run e2e      # Playwright; one-time: npx playwright install chromium
```

From the repo root, `just web-test` mirrors the first line, `just web-e2e` mirrors the e2e job,
and `just web-contract` mirrors the contract-drift guard. If `just web-test` is green and
`just web-e2e` is green, CI's `web-*` jobs will be too.

## The layer DAG

Imports flow **one direction only**: a lower layer may never import an upper one. This is the
single biggest collision guard — e.g. the pure `lib/format.ts` units core can never accidentally
pull in React or a page. Boundaries are enforced by `eslint-plugin-boundaries` in
`eslint.config.js`; a violation fails `npm run lint`.

| Layer        | Folder                              | May import                                | Why |
|--------------|-------------------------------------|-------------------------------------------|-----|
| `lib`        | `src/lib/`                          | `lib`                                      | The floor: pure, framework-free helpers (number/units formatting, vol math). No React, no fetch. |
| `api`        | `src/api.ts`, `src/api/`, `src/stressApi.ts` | `lib`, `api`                       | The typed BFF client. Knows wire shapes, nothing about the UI. |
| `domain`     | `src/domain/`, `src/basketTemplates.ts` | `lib`, `api`, `domain`                | Framework-free view-models built over the wire contract. Still no React. |
| `ui`         | `src/ui/`                           | `lib`, `ui`                                | shadcn primitives. Reusable, app-agnostic; never reach up into app concerns. |
| `hooks`      | `src/hooks/`                        | `lib`, `api`, `domain`, `ui`, `hooks`      | Data hooks over the client + domain models. |
| `components` | `src/components/`                   | `lib`, `api`, `domain`, `ui`, `hooks`, `components` | Presentational / chart components that compose primitives and hooks. |
| `feature`    | `src/pages/`                        | everything below + `feature`              | The pages. Compose everything under them. |
| `app`        | `src/main.tsx`, `src/App.tsx`, `src/routes.ts` | everything                      | The shell that wires features together. |

**Cross-feature isolation:** pages do not import each other's internals. The `feature`→`feature`
allowance exists only until features get their own folders; treat anything shared between two pages
as a `components`/`domain`/`hooks` extraction, not a reach into a sibling page. Import cycles are
banned outright (`import/no-cycle`), and imports are auto-sorted (`simple-import-sort`) so two
people editing the same import block don't produce conflicting diffs.

## Where does my code go?

- **A new page** → `src/pages/MyPage.tsx`. Add it to **`src/routes.ts`** (path + label + the `<h1>`
  heading) and bind the component in `src/App.tsx`'s `PAGES` map. `routes.ts` is the single source
  of truth for the nav, the route table, and the e2e collision net — add it there once and all
  three pick it up.
- **A reusable primitive** (button, input, dialog…) → `src/ui/`. Add a shadcn primitive with
  `npx shadcn@latest add <name>` (config in `components.json`: new-york style, `@/ui` alias,
  `@/lib/utils` for `cn`). It lands themed onto the dark operator palette — keep it app-agnostic.
- **A presentational or chart component** → `src/components/`. Charts use Lightweight Charts
  (candles / line term-structure) or Plotly (3D / heatmap / waterfall) per the README.
- **A data fetch** → a hook in `src/hooks/queries.ts` using TanStack Query. The page asks for a
  domain thing by name; it never calls `fetch` or `getJson` directly. Follow the query-key
  convention (below).
- **A pure helper** (formatting, units, math) → `src/lib/`. No React, no fetch.
- **A view-model over wire types** → `src/domain/`. Framework-free shaping of `api` types.

### Query-key convention

Keys are tuples, **broadest scope first**, so React Query's prefix matching can invalidate a whole
family at once:

```
[domain, resource, ...params]

["risk", "portfolios"]              // a list, no params
["risk", "scenarios", portfolioId]  // scoped to one selection ("" = all-portfolios view)
```

Keep params in a stable order and include only what actually scopes the request, so two call sites
asking for the same data share one cache entry. Forward React Query's `AbortSignal` to `api.ts` so
an unmount / key change cancels the in-flight fetch.

## Number display rule

Every quantitative analytics number on screen renders via **`src/lib/format.ts`**: scientific
notation, six significant figures (trailing zeros stripped), and **always with its unit**
(`sci` / `sciUnit`, units from the `UNITS` vocabulary). Never put a bare analytics number on
screen. Pure cardinalities (counts), dates, ids, and enum labels are not analytics quantities and
keep their plain rendering. Dollar/PnL units carry `$` as a currency placeholder — render the real
currency with `withCurrency` driven by the index's quote currency, never a hard-coded `$`.

## Contract workflow

The web app's wire types come from the BFF's OpenAPI schema. When the BFF contract changes:

```bash
just web-contract   # re-exports openapi.json, regenerates src/api/schema.d.ts, fails on drift
```

Commit **both** `openapi.json` and `src/api/schema.d.ts`, or CI's `web-contract` job fails on the
un-regenerated drift.

**Current limitation:** the BFF's GET endpoints return untyped `JSONResponse` dicts (no pydantic
`response_model=`), so they do not appear in the OpenAPI schema and the generated types are thin
today — the GET response shapes are hand-maintained in `src/api.ts` and mirror the BFF's
`serializers.py`. Enriching the routers with `response_model=` is the high-leverage follow-up (see
`BIG_PICTURE` / ADR 0030) that turns this guard into real type-level drift detection end to end.

## Testing expectations

- **Component / unit:** Vitest + Testing Library + MSW (jsdom). For a page or component that uses
  TanStack Query, render with `renderWithClient` (`src/test/renderWithClient.tsx`) so it gets a
  fresh QueryClient per test.
- **End-to-end / layout:** Playwright in real Chromium for anything with layout or charts —
  **jsdom has no layout engine**, so element overlap, off-screen controls, and horizontal overflow
  can only be checked here. The BFF is mocked at the network layer (`e2e/mock-bff.ts`) with the
  same fixtures the component tests use.
- **New routes** MUST be added to the shared route list in `src/routes.ts`. The layout/collision
  net (`e2e/layout.spec.ts`) iterates that list across desktop/laptop/narrow viewports, so a new
  page is auto-covered the moment it ships.

## Collision rules of thumb

The "obvious to senior FE devs" list — most are lint-enforced, all are reviewed:

- **No new global CSS classes.** Globals are design tokens only; compose primitives and Tailwind
  utilities on the element instead.
- **No cross-layer imports** (the DAG above).
- **No duplicate components** — search `src/components/` and `src/ui/` before adding one.
- **Deterministic imports** — let `simple-import-sort` order them; don't fight it.
- **No bare numbers** — analytics values go through `format.ts` with their unit.

## Resurfacing map

Queued page work lives in [`../../../tasks/frontend-*.md`](../../../tasks/) (attribution view,
capture-coverage panel, named-scenarios wiring, page-1 build-out, per-side surfaces toggle,
scenario rate-axis, second-order Greeks, sig-fig display). The highest-leverage new-feature targets
are backend products that are **built but not yet surfaced** — they need a BFF router and a page:

- **Signals layer** — implied correlation, term-structure slope, RV−IV spread, IV rank.
- **Backtester results.**
- **Strategy / portfolio-level P&L** (TARGET §5.8).
