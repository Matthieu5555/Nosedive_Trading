# MAT-LEGIBILITY-skeletons — loading is a designed state, and no component is ever blank

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on… when something
> breaks the user sees it, a red message, never a blank chart." Today the *error* and *empty* halves of
> that promise are kept — every chart has a named empty state and a loud red banner. The *loading* half is
> not: a 440px-tall nappe is replaced, while it fetches, by the literal one-line text `Loading…`
> (`AsyncBlock.tsx:10-14`). The panel collapses to a single line, then the chart pops back in and the page
> reflows on every selector change. A PM watching the page jump cannot tell *what is about to appear* or
> *whether anything is wrong*. This is Principle 3 ("No silent state. Ever.") delivered at its weakest
> link — and the policy that keeps it from regressing: a component-test rule that **no surface is ever
> blank**, in any of its three states.

> **Cross-spec reconciliation (canonical — see [MAT-LEGIBILITY-build-order]).** This spec is the **sole
> owner** of the `<ChartSkeleton>` primitive, the `AsyncBlock` loading-branch swap, the reduced-motion CSS,
> and the never-blank test policy. Two adjacent specs **consume** this primitive and must not fork a second
> `AsyncBlock` edit: [MAT-LEGIBILITY-action-feedback] and [MAT-LEGIBILITY-design-system]. One behaviour
> [MAT-LEGIBILITY-action-feedback] needs — the **`<1 s` delay-floor (`SKELETON_DELAY_MS ≈ 1 000 ms`)** before
> the skeleton mounts, and the **optional subject name** (*"Chargement de la nappe SX5E…"*) — should land
> **here, with the primitive**, since both specs touch the same `AsyncBlock` loading branch and editing it
> twice is the collision the owner-lane warns about. (The design-language P4 table fixes the `<1 s → no
> loader` rule; honour it in the same change. Subject-naming is already in this spec's Design intent below.)

## What's true today (grounded in code)

- **One shared loading primitive, one bare string.** `AsyncBlock` (`components/AsyncBlock.tsx:9-25`) is the
  single loading/error wrapper used on every page — `Market.tsx` (8 call sites: `:100`, `:123`, `:152`,
  `:176`, `:188`, `:204`), `Positions.tsx`, `Signals.tsx`, `Operations.tsx`, `RiskScenarios.tsx`,
  `Strategy.tsx`, `CoverageTable.tsx:132`, `RunControlPanel.tsx:127/:180`, `ConstituentsWorkspace.tsx`,
  `BookSection.tsx`. Its loading branch renders `<div className="state-panel" role="status">Loading…</div>`
  (`:10-14`) — a one-line text, **no footprint, no subject**. Fix it once and every page improves.
- **The loading box has no height.** `.state-panel` (`index.css:1085-1096`) is a 14px-padded box that wraps
  its text; it does not reserve the height of the chart it stands in for. So a tall panel
  (`Plot` defaults to `height = 440`, `Plot.tsx:18`) reflows the whole `market-scroll` when content arrives.
- **Error and empty are already right — copy them.** The error branch is a loud
  `role="alert"` (`AsyncBlock.tsx:17-23`, `index.css:1098-1102` red palette); the global surface is
  `GlobalErrorBanner` (`role="alert" aria-live="assertive"`). Every chart names its empty subject:
  `PriceChart` (`charts.tsx:29-36`, *"No daily bars for {underlying}…"*), `VolSurface :165-172`,
  `SmileChart :257-264`/`:296-303`, `TenorPanel :86-91` (the projection-gap `role="status"`). The loading
  state is the only one of the three that is *not* a designed state.
- **No skeleton exists anywhere** (grep `skeleton` over `src/**` is empty), and there is **no
  `prefers-reduced-motion` rule** in `index.css` — a shimmer added naively would animate for users who
  asked the OS not to.
- **No `AsyncBlock.test.tsx` exists.** The primitive that gates every page's loading state is untested.

## Objective

Two deliverables, one theme:

1. **A skeleton loading state.** Replace the bare `Loading…` in `AsyncBlock` with a footprint-preserving
   **`<ChartSkeleton>`** that reserves the panel's real height and reads as *"this is loading, nothing is
   wrong,"* not *"this is broken/empty."* One change to the shared primitive lifts every page.
2. **A never-blank component-test policy.** A reusable test helper + a written rule (in
   `apps/frontend/README.md`) asserting that every async surface renders **something legible and
   correctly-toned in all three states** — loading, empty, error — and that the three **read differently**.
   This is the regression net for Principle 3 across the app, not just for this one component.

The owner's test for both: *can the PM tell what they're looking at* — including "it's loading, wait" —
*and would they ever be misled* (a blank that reads as broken, a skeleton that reads as real data, an empty
that reads as an error)?

## Design intent (this is a designed state, not a spinner dump)

The 2026 thresholds are normative (design-language P4 table): **< 1 s → no loader; ~1–9 s → skeleton;
10 s+ → determinate step progress.** Our fetches sit in the skeleton band, so a skeleton is the right tool
here; step-based progress on long *jobs* is a different task ([MAT-LEGIBILITY] sibling / P4) and out of
scope. The bar:

- **Footprint, not text.** The skeleton occupies the same height the real content will (`Plot`'s `height`,
  default 440 — `Plot.tsx:18`), so the chart fades in *in place* with **zero reflow**. A skeleton that does
  not reserve height has failed its one job.
- **Reads as loading, distinct from empty and error.** Three states, three reads, reusing the existing
  palette — no new accent (P7):
  - **loading** → neutral skeleton, `role="status"`, an accessible *"Chargement…"* name; muted, patient,
    clearly *not yet data*.
  - **empty** → an *affirmative* named sentence (the chart's job, already done; e.g. `VolSurface :169`).
  - **error** → loud red `role="alert"` (already `AsyncBlock.tsx:17-23`).
  They must never be confusable: a skeleton must not look like rendered data (no fake numbers, no real
  axes), and an empty state must not read like an error.
- **Quiet motion, or none.** A shimmer is optional and must be **disabled under
  `prefers-reduced-motion: reduce`** — add the media query to `index.css`. A static muted block is an
  acceptable skeleton; an always-animating shimmer that ignores the OS preference is not.
- **Subject when cheap.** Where the caller can pass it, the skeleton names what is loading
  (*"Chargement de la nappe SX5E…"*) — same self-describing instinct as §2b. Where it can't, a plain
  *"Chargement…"* is acceptable; a skeleton must never *claim* a subject it doesn't have.
- **Plain words, PM register** (`analytics-pm-legible-framing`): *"Chargement…"*, never `Loading…`,
  never `Fetching snapshot…`. A label labels.

## Owns

- **`<ChartSkeleton>`** — a small presentational component: a muted block of a given `height` (default
  matching `Plot`'s 440), `role="status"`, accessible name *"Chargement…"* (or a passed subject), optional
  shimmer gated on reduced-motion. Lives in `components/`.
- **`AsyncBlock` wired to it** (`components/AsyncBlock.tsx`): loading branch renders `<ChartSkeleton>`
  instead of the bare text. Add an **optional** `height?: number` and `subject?: string` prop so a caller
  *can* size/name the skeleton; the prop is additive — every existing call site keeps working with the
  default. Error and empty branches are unchanged.
- **CSS** (`index.css`): a `.chart-skeleton` rule (height-reserving, muted, optional shimmer) and a
  `@media (prefers-reduced-motion: reduce)` rule that stills it. Reuse existing tokens
  (`--panel-soft`, `--border-strong`); no new accent.
- **`AsyncBlock.test.tsx`** — the first test of the shared primitive (loading → skeleton with
  `role="status"`; error → `role="alert"` with the message; children when settled).
- **A reusable never-blank assertion** — a small helper in `src/test/` (e.g.
  `assertNeverBlank(renderResult)`) that fails if the rendered output is empty / whitespace-only, and the
  README rule that says to use it for every async surface.
- **README policy** (`apps/frontend/README.md`, the test section ~`:35-60`): document the three-state
  never-blank rule and the helper.

## Depends on / coordinates with

- **Touches the shared `AsyncBlock`** — every page reads it. Change is additive (props default, loading
  branch swaps text→skeleton); no call site needs editing. Run the **full web suite** (`tsc + lint +
  vitest + playwright`), not just the new test, because every page's loading path changes.
- **Frontend is owner-owned** (`frontend-is-owner-owned`): `apps/frontend/web` is Matthieu's lane. If a
  fleet agent picks this up, claim the `AsyncBlock` row on the TASKBOARD first and coordinate — this is the
  one file that collides with everything.
- **Sibling of [MAT-LEGIBILITY-coverage-headline] / -quarantine-drilldown / -strict-indicative-mode.** Those
  add *content*; this hardens the *loading* state under all of them. Independent — ship in any order.
- **No backend, no BFF, no new data.** Pure front surfacing of a state we already have.

## What to do (ordered)

1. **`<ChartSkeleton>`.** Presentational, `role="status"`, default `height = 440`, accessible name
   *"Chargement…"* (override via `subject`). Static muted block by default; optional shimmer class.
2. **CSS.** `.chart-skeleton` reserves `height`, muted fill, rounded like `.state-panel`. Add
   `@media (prefers-reduced-motion: reduce) { .chart-skeleton { animation: none; } }`.
3. **Wire `AsyncBlock`.** Loading branch → `<ChartSkeleton height={height} subject={subject} />`. Add the
   two optional props with defaults; leave error/empty branches and all call sites untouched.
4. **Test the primitive.** `AsyncBlock.test.tsx`: loading renders a `role="status"` skeleton of non-zero
   footprint and the *"Chargement…"* name (no `Loading…` text leaks); error renders `role="alert"` with the
   message; settled renders children.
5. **Never-blank helper + README rule.** Add `assertNeverBlank` to `src/test/`; document the three-state
   policy in `apps/frontend/README.md`; apply the assertion in at least the `Market` loading test as the
   worked example.
6. **Optionally pass `height`/`subject`** at the tall Market call sites (the nappe `:176`, scorecards
   `:123`) so the skeleton sizes to the real chart — proves the prop end to end. Not required for the other
   pages (default height is fine).

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **Loading is a designed state (component test).** `AsyncBlock` with `loading` renders a `role="status"`
  element whose accessible name is *"Chargement…"* and whose **rendered height is non-zero** (assert the
  inline/`style` height or class, not internal state). Assert the text `Loading…` is **gone**.
- **The three states read differently (component test).** For one surface, render loading / empty / error
  and assert: loading is `role="status"` (skeleton), empty is the *named affirmative sentence* (e.g. the
  `VolSurface` empty copy), error is `role="alert"` with the message — three distinct roles/reads, never the
  same DOM. The owner test: a PM can tell *loading* from *broken* from *nothing-here* without guessing.
- **Reduced-motion (component or e2e/CSS).** Under `prefers-reduced-motion: reduce` the skeleton does not
  animate. (jsdom can't evaluate media queries — assert the rule exists via the CSS/Playwright path, or that
  the component emits the static class; don't fake a green.)
- **Never-blank policy bites.** `assertNeverBlank` fails on an empty render; wire it into the Market loading
  test and (worked example) show it would have caught the bare-text regression. Extend the policy to new
  async surfaces.
- **No-reflow (Playwright, opt-in).** On a Market load, the panel that shows the skeleton occupies
  ~the chart's final height — i.e. the surrounding layout does not jump when data arrives. This is exactly
  what jsdom structurally can't see; it's why the e2e suite exists (`apps/frontend/README.md`).
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` **and** the
  web suite (`npm run lint && npm test`), plus `npm run e2e` for the no-reflow check when you touch layout.

## Done criteria

`AsyncBlock`'s loading branch renders a footprint-preserving `<ChartSkeleton>` (`role="status"`,
*"Chargement…"*, real height) instead of the bare `Loading…`; switching the Market underlying no longer
collapses the panel to one line and reflows the page; the skeleton stills under `prefers-reduced-motion`;
`AsyncBlock` is tested for the first time; a `assertNeverBlank` helper + a written three-state policy in
`apps/frontend/README.md` make "never blank" a regression-tested rule; loading, empty, and error read
**differently** on screen and in the tests; both gates green; no backend change.

## Gotchas

- **Surface only, no new data.** This dresses a state we already have. Nothing fetched, nothing computed.
- **Reserve the height or it's pointless.** A skeleton that doesn't occupy the chart's footprint still
  reflows — that is the whole bug. Default to `Plot`'s 440 and let callers override.
- **Don't make a skeleton look like data.** No fake axes, no placeholder numbers, no real legend. A skeleton
  that reads as a real (empty) chart is a worse lie than the bare text — it's the "silent green" failure
  mode in a new costume.
- **Respect reduced-motion.** No always-on shimmer. Add the media query in the same change, or ship it
  static.
- **Additive props, untouched call sites.** `height`/`subject` default; do not edit the 20+ `AsyncBlock`
  call sites except the optional Market worked example. Touching them all is how a one-file change becomes a
  shared-tree collision.
- **Don't reinvent the design system** (P7). Reuse `--panel-soft`/`--border-strong`, the `.state-panel`
  shape, `role="status"`/`role="alert"`. The skeleton speaks the existing vocabulary; spend no boldness on a
  new accent — the boldness here is that the page no longer jumps.
- **Plain words.** `Chargement…`, not `Loading…`/`Fetching…`. A label labels (`analytics-pm-legible-framing`).
