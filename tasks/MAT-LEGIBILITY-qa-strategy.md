# MAT-LEGIBILITY-qa-strategy — the Playwright e2e contract that makes "would the PM be misled?" a test

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on." The three
> [MAT-LEGIBILITY] specs and the two design-language docs ([frontend-design-language-2026],
> [frontend-design-language-2026-examples]) say *what right looks like* as ✅/❌ pairs. A pair the
> owner cares about that no test enforces is a pair that silently rots. This spec turns every ✅/❌ pair
> in both design docs into a concrete, real-browser **assertion** — so the moment a label stops tracking
> its data, a skeleton degrades to a bare "Loading…", or a market-closed surface renders silent-green,
> a red test says so. The owner's one line is the test oracle for every row below: *can the PM tell what
> they're looking at, and would they ever be misled?*

## What's true today (grounded in code — measured, not assumed)

The running web app is **not** the French Données/Risque/Ordres UI the design docs and
`apps/frontend/README.md` describe. The shipped surface is the **English seven-tab** console:

- `apps/frontend/web/src/routes.ts:15-23` defines `ROUTES` as seven tabs (`Market`, `Basket`,
  `Signals`, `Strategy`, `Risk Scenarios`, `Positions`, `Operations`), all English, `heading: "Market"`.
  `e2e/navigation.spec.ts:7-19` asserts exactly these seven and that `Données/Risque/Ordres` are **absent**.
- The Market page heading is `<h1>Market</h1>` (`Market.tsx:73`), not `Données`. The nappe panel is
  `aria-label="Volatility surface"` with a **static** `<h2>Volatility nappe</h2>` and a static caption
  `<span class="status">all maturities</span>` (`Market.tsx:167-173`). The status line above it is
  `{index} · as of {effectiveAsOf} <QcBadge/>` (`Market.tsx:116-120`) — the date, **no close instant**.
- The nappe figure's own label is the **constant** `SURFACE_LABEL`
  (`charts.tsx:40` = `"Implied-volatility surface (vol vs log-moneyness vs maturity)"`), rendered as the
  `<figcaption>`/`aria-label` by `Plot.tsx:20-21`. It carries `⚠ {flaggedNote}` when slices are railed
  (`charts.tsx:112,164`) but never the underlying, date, mode, or coverage.
- Loading everywhere on Market is the bare text `Loading…` in a one-line `state-panel`
  (`AsyncBlock.tsx:10-14`) — no skeleton, no reserved height.
- There is **no** coverage headline, **no** strict/indicative toggle, **no** ⓘ hotspot, **no** assistant,
  **no** job-progress narration. Those are the unbuilt MAT-LEGIBILITY / design-language targets.

So this spec has two jobs at once, and **keeps them separate**:

1. **Lock the truths that already hold** (regression guard) — active tests, must stay green.
2. **Pre-write the truths the MAT-LEGIBILITY specs will make hold** (executable acceptance criteria) —
   `test.fixme`-skipped today, the implementing agent removes the skip the same commit it ships the
   feature. A skipped test names the future contract precisely; it is the acceptance gate, not a wish.

The existing e2e suite (`e2e/{navigation,layout,pages,market-read-flow,operations,basket-flow}.spec.ts`)
already does (1) well for nav/layout/read-flow. This spec extends it to the **legibility** pairs the
design docs add, and gives the house a single matrix mapping every pair → its test.

## Objective

A **Playwright e2e strategy** plus a **pair → test acceptance matrix** such that:

- Every ✅/❌ pair in [frontend-design-language-2026] (§2b, §3, §4, §5, §6, §7) and in
  [frontend-design-language-2026-examples] (all seven principles) maps to **exactly one named test**
  (`file::title`) — built or `fixme`-skipped — and that mapping is in this doc, checkable by `grep`.
- The tests assert **user-visible text, role, and tone** (the PM's read), never internal React state,
  exactly as the existing suite does (`market-read-flow.spec.ts:56-125` is the template).
- The "would they be misled?" pairs (silent-green, stale title, fabricated mid, hallucinated number)
  each get a test that **fails when the lie reappears**, driven off a fixture that reproduces the
  dangerous state (closed market, 30%-excluded day, no-quote strike).

This spec is the QA half of the legibility cluster. It owns **no product code** — it owns the test
suite and the fixtures the suite needs.

## Design intent (this is a test contract, not a coverage-number chase)

- **Real browser, mocked BFF — same as today.** Every test mocks the BFF at the network layer via
  `e2e/mock-bff.ts` (the same contract fixtures as the component tests), never touches a live BFF or
  `data/` (`playwright.config.ts:1-15` says so; honor it). Determinism is non-negotiable: a flaky
  legibility test trains the eye to ignore red, the exact anti-pattern §3 names.
- **One fixture per dangerous state.** The misleading-state pairs need fixtures that *are* the danger:
  - `ANALYTICS_MARKET_CLOSED` — zero two-sided rows / degenerate slice → the silent-green canary.
  - `ANALYTICS_PARTIAL_COVERAGE` — the 2026-06-17 shape (e.g. 1 706 / 2 412 two-sided).
  - `ANALYTICS_QUOTED` already exists (`src/test/fixtures.ts`, the no-quote `30dp` strike) — reuse it for
    the fabricated-mid pair; do **not** fork it.
  Fixtures live with the others in `src/test/fixtures.ts` so component and e2e share one source.
- **Assert tone, not colour hex.** Tone rides roles/aria and the QC palette classes (`role="alert"` for
  error, `role="status"` for empty/loading, `qc-badge--{verdict}` / `ops-pill--{tone}`). Assert the
  *role and the words*, never a computed `rgb()` — colour is a design token, the contract is "the PM sees
  an alarm, in alarm words."
- **`fixme`, not delete, for unbuilt pairs.** Use `test.fixme(title, fn)` with the future assertion
  written out. `grep "test.fixme" e2e/` lists the open legibility frontier; each `fixme` cites the
  MAT-LEGIBILITY spec that retires it. An agent shipping that feature flips `fixme`→`test` and the gate
  proves the pair.
- **No new test runner, no new helper vocabulary.** Reuse `e2e/helpers.ts`
  (`collectPageErrors`, `expectNoCollisions`, `expectNoHorizontalOverflow`, `expectWithinViewport`) and
  the `mockBff` override pattern (`market-read-flow.spec.ts:50-54`: route handlers run most-recent-first,
  so a per-test override of `**/api/analytics**` wins). Principle 7 (one design system) applies to the
  test code too.

## Owns

- **New spec file** `e2e/legibility.spec.ts` — the self-describing-label, no-silent-state,
  action-feedback, and grounded-assistant assertions (the pairs the existing specs don't cover).
- **Fixture additions** in `src/test/fixtures.ts`: `ANALYTICS_MARKET_CLOSED`, `ANALYTICS_PARTIAL_COVERAGE`
  (+ any `coverage` block the MAT-LEGIBILITY-coverage-headline BFF lands). Additive, shared with the
  component suite. Do not duplicate `ANALYTICS_QUOTED`/`ANALYTICS_AAA`.
- **This matrix**, kept honest: when a `fixme` flips to a live test, its matrix row's status flips too.
- Tests only. No product `src/` change is in this spec's scope (frontend is owner-owned — `frontend-is-owner-owned`).

## Depends on / coordinates with

- **Reads the same fixtures as the component tests** — never invents a contract. A fixture shape that
  the BFF can't actually emit is a bug in the fixture.
- **The three feature specs gate the `fixme` flips.** A `fixme` titled "nappe title carries subject ·
  as-of · mode · coverage" turns green only when [MAT-LEGIBILITY-coverage-headline] +
  [MAT-LEGIBILITY-strict-indicative-mode] land the dynamic `SURFACE_LABEL` (examples doc shortlist item 1,
  `charts.tsx:40`→a function). The matrix row points at the spec that owns the flip.
- **Shared-tree:** `e2e/` is owner-authored (`matthieu`, per `ls -la`). New e2e files are additive and
  disjoint from `src/`; claim the `e2e/legibility.spec.ts` row on the TASKBOARD so two agents don't both
  create it. `src/test/fixtures.ts` is a shared seam — coordinate the additive fixture block.
- **Opt-in, not in the gate** (`AGENTS.md:106-112`): e2e needs a browser binary + dev server. This spec
  does **not** propose wiring it into `npm test`; it proposes that an agent touching a page **runs**
  `npm run e2e` and keeps it green, and that the legibility frontier lives as visible `fixme`s.

## What to do (ordered)

1. **Add the two danger fixtures** to `src/test/fixtures.ts`:
   `ANALYTICS_MARKET_CLOSED` (degenerate: every `surface_slice.degenerate = true`, zero two-sided rows,
   and — once the BFF lands it — `coverage: { option_rows, two_sided: 0, excluded: option_rows,
   two_sided_fraction: null }`); `ANALYTICS_PARTIAL_COVERAGE` (`coverage` with `two_sided: 1706`,
   `option_rows: 2412`, `excluded: 706`, `two_sided_fraction ≈ 0.7073`). Derive the arithmetic by hand,
   never from the code under test (mirror `MAT-LEGIBILITY-coverage-headline.md:99-106`).
2. **Write `e2e/legibility.spec.ts`** with the tests named in the matrix. Build the ones whose feature
   already exists (the §2b "title carries quality", §3 empty/error, §7 sign-legend pairs the current code
   *already* satisfies). `test.fixme` the ones whose feature is unbuilt, with the full assertion written.
3. **Wire `collectPageErrors`** into every legibility test and assert `pageErrors == []` (a crash is the
   loudest silent-state failure). Mirror `market-read-flow.spec.ts:59,124`.
4. **Keep the matrix in this file in lockstep** with the spec — every test in `legibility.spec.ts` is a
   matrix row and vice versa; CI-grep-able (`title` strings match).
5. **Run it:** `cd apps/frontend/web && npx playwright install chromium && E2E_PORT=<free> npm run e2e`.
   Green = built rows pass, `fixme` rows skip (reported, not failed).

## The acceptance matrix — every design-doc ✅/❌ pair → one test

Status legend: **LIVE** = built, must pass today. **FIXME** = `test.fixme`, the future contract;
the named spec retires it. Each row's oracle is the PM read in the app's own UI vocabulary.

### Principle 2b — self-describing components (every label binds to live state)

| # | ✅ the PM must see / ❌ the lie it forbids | Test (`file::title`) | Status | Retired by |
|---|---|---|---|---|
| 2b.1 | ✅ Nappe figure title names quality: `… — ⚠ N slices flagged` when slices railed. ❌ a clean title over a railed fit. | `legibility::nappe title shows the flagged-slice warning when a fit is railed` | LIVE | — (already `charts.tsx:112,164`) |
| 2b.2 | ✅ `PriceChart` title interpolates the underlying (`SPX — daily price (OHLC candlestick)`); switch the index and it changes. ❌ a constant "Price" title. | `legibility::price chart title carries the selected underlying` | LIVE | — (`charts.tsx:28`) |
| 2b.3 | ✅ `SmileChart` title carries the selected tenor and `⚠ degenerate fit` on a degenerate slice. ❌ "Smile" with no tenor / no degenerate flag. | `legibility::smile title carries tenor and degenerate-fit warning` | LIVE | — (`charts.tsx:272-274`) |
| 2b.4 | ✅ Nappe **title** carries `subject · as-of · mode · coverage` (`Nappe — SX5E · 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations`). ❌ the constant `SURFACE_LABEL` that names no index/date/mode/coverage (examples §2b.2). | `legibility::nappe figure title carries subject as-of mode and coverage` | FIXME | coverage-headline + strict-indicative-mode (`charts.tsx:40` → fn of `(underlying,asOf,mode,coverage)`) |
| 2b.5 | ✅ The visible panel `<h2>` is `Nappe de volatilité — SX5E` with caption `clôture 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations`. ❌ the static `<h2>Volatility nappe</h2>` + `all maturities` (examples §2b.1, `Market.tsx:171-173`). | `legibility::nappe panel heading and caption track underlying date mode coverage` | FIXME | coverage-headline (the `Market.tsx` mount) |
| 2b.6 | ✅ The status line states the **close instant**: `as of 2026-06-17 17:30 CET (close)` (SX5E 17:30, `sx5e-close-instant-1730-cet`). ❌ `as of 2026-06-17` with no instant (`Market.tsx:118`). | `legibility::market status line states the close instant not just the date` | FIXME | (examples §2 as-of fix) |
| 2b.7 | ✅ Switch the underlying selector → title + caption + (coverage headline) + axes all change **together**. ❌ one updates, another stays stale (two labels disagree on one screen). | `legibility::switching the index updates every self-describing label in one paint` | FIXME | coverage-headline + strict-indicative-mode (one-state drive, examples §2b.3) |
| 2b.8 | ✅ Axes carry unit in the house idiom (`log-moneyness (k)`, `implied vol` %, `strike`). ❌ an unlabeled/unitless axis. | `legibility::smile and greeks axes carry their units` | LIVE | — (`charts.tsx:236-237,310-311`) |
| 2b.9 | ✅ Smile legend names the actual wings plotted (`puts`, `calls`); a wing with no points leaves the legend. ❌ `Series 1`/`Series 2`. | `legibility::smile legend names the put and call wings actually plotted` | LIVE | — (`charts.tsx:294-295`) |

### Principle 2 — legibility & provenance (numbers carry unit + where-from)

| # | ✅ / ❌ | Test | Status | Retired by |
|---|---|---|---|---|
| 2.1 | ✅ Every scorecard is label + value + hint; a null reads `—`, never blank (`Scorecards.tsx:124-126,6-17`). ❌ a bare number or an empty cell. | `legibility::scorecards render label value and hint with em-dash for nulls` | LIVE | — |
| 2.2 | ✅ The sign legend is on the band in plain PM words (`RV−IV > 0 = vol cheap (buy)`, `Scorecards.tsx:131-136`). ❌ a coloured number with no legend. | `legibility::scorecard band carries the plain-language sign legend` | LIVE | — |
| 2.3 | ✅ A hover/ⓘ on a metric states what it is / how computed / what it excludes, in PM register (provenance on demand). ❌ no where-from affordance (examples §2 "no where-did-this-come-from"). | `legibility::an info dot on a metric reveals its what-and-where-from gloss` | FIXME | (examples shortlist 3 — the `<InfoDot>` + explanation map) |
| 2.4 | ✅ Every quantitative number reaching the screen carries its unit via `sciUnit`/`UNITS`. ❌ a naked number through `Metric.tsx` (examples §2 "`Metric` bypasses the number law"). | `legibility::no metric renders a naked unitless number` | FIXME | (examples §2 `Metric.tsx` fix) |

### Principle 3 — no silent state, ever (loading / empty / error read differently)

| # | ✅ / ❌ | Test | Status | Retired by |
|---|---|---|---|---|
| 3.1 | ✅ Empty smile/surface is an honest *named* empty state, never a blank chart (`charts.tsx:169,263,302,333`; `TenorPanel.tsx:89-91`). ❌ a blank `<figure>`. | `legibility::an uncaptured tenor shows a named projection-gap, never a blank chart` | LIVE | — |
| 3.2 | ✅ A fetch failure renders `role="alert"` loud copy (`AsyncBlock.tsx:17-23`); app-wide failures hit the `role="alert" aria-live="assertive"` banner (`GlobalErrorBanner.tsx:21`). ❌ a silently dead tile. | `legibility::a failed analytics fetch shows a loud alert, not a blank panel` | LIVE | — |
| 3.3 | ✅ **The silent-green canary:** a market-closed/degenerate surface says, in error tone, `Surface indicative — marché probablement fermé`. ❌ a plausible nappe off a closed market with nothing on screen saying so (the original sin, design-doc anti-pattern #2, coverage-headline `:50-51`). | `legibility::a market-closed degenerate surface is loudly flagged, never silent-green` | FIXME | coverage-headline + strict-indicative-mode (`ANALYTICS_MARKET_CLOSED`) |
| 3.4 | ✅ Loading reserves the chart's footprint (a skeleton). ❌ a 480px nappe popping in from a one-line `Loading…`, reflowing the layout (examples §3, `AsyncBlock.tsx:10-14`). | `legibility::a loading nappe shows a footprint skeleton, not a bare one-line Loading text` | FIXME | (examples shortlist 2 — `AsyncBlock` skeleton) |
| 3.5 | ✅ Empty copy **names its subject** (`Aucune nappe pour SX5E au 2026-06-17 — marché probablement fermé`). ❌ generic `No surface to plot yet` (examples §3, `charts.tsx:169`). | `legibility::the empty nappe state names the subject and as-of` | FIXME | coverage-headline (examples §3 empty-copy fix) |
| 3.6 | ✅ Empty ≠ error: the affirmative full-coverage state (`Aucune cotation exclue — couverture complète`) reads `role="status"`; the closed-market state reads `role="alert"`. ❌ both rendered identically. | `legibility::affirmative empty and error states read with different roles and words` | FIXME | coverage-headline (`ANALYTICS_PARTIAL_COVERAGE` vs `ANALYTICS_MARKET_CLOSED`) |
| 3.7 | ✅ No flow leaves an uncaught page error (a crash is the loudest silent failure). ❌ a `pageerror` anywhere in the read flow. | every legibility test asserts `errors.pageErrors == []` (helper `collectPageErrors`) | LIVE | — |

### Principle 4 — every action explains itself; long processes narrate

| # | ✅ / ❌ | Test | Status | Retired by |
|---|---|---|---|---|
| 4.1 | ✅ The launch button flips `Launch run`→`Launching…` and disables while pending; the jobs table shows a state pill ledger; empty case is the honest `No runs launched this session yet…` (`RunControlPanel.tsx`; `operations.spec.ts:34-44` already asserts the flow). ❌ a button with no feedback. | `operations::an operator can launch a run and watch the job list` | LIVE | — (existing) |
| 4.2 | ✅ The launch button's hover/ⓘ says what fires underneath (`Fetches a fresh option chain from {provider} and rebuilds the surface — writes a new capture run`). ❌ a mystery verb (examples §4 "button doesn't say what it does underneath"). | `legibility::the launch button reveals the backend action it fires` | FIXME | (examples §4 action-gloss) |
| 4.3 | ✅ A 10s+ capture shows **determinate/step** progress with the stage name (`solving IV, 1 706 points…`), not just a queued→running pill. ❌ a coarse state flip with no narration (examples §4 "no progress for the long job"). | `legibility::a running capture narrates determinate step progress` | FIXME | (examples shortlist 4 — job progress) |
| 4.4 | ✅ Changing index/as-of shows a visible "fetching {index} {date}…" affordance. ❌ the panel silently swapping to `Loading…` (examples §4 "selectors refetch silently"). | `legibility::changing the index shows a visible fetching affordance` | FIXME | (examples §4 / shortlist 2) |

### Principle 5 — contextual guidance that points, flashes, gets out of the way

| # | ✅ / ❌ | Test | Status | Retired by |
|---|---|---|---|---|
| 5.1 | ✅ Always-on inline micro-glosses exist where curvature/signs are read (the convexity formula hint `TenorPanel.tsx:27-29`; the scorecard sign legend). ❌ a number with no in-place explanation. | `legibility::the convexity readout carries its butterfly formula gloss` | LIVE | — |
| 5.2 | ✅ Any element can hang a `<InfoDot>` tooltip that opens its "what is this / how to read it" on hover/click, non-modal. ❌ zero interactive help (examples §5 "zero interactive contextual help"). | `legibility::an info dot opens a non-modal what-is-this tooltip` | FIXME | (examples shortlist 3 — `<InfoDot>`) |
| 5.3 | ✅ On first load with no index chosen, the index selector pulses a next-step hint; it stops once chosen. ❌ a front-loaded modal tour, or no cue at all (examples §5; `Market.tsx:26` `index === ""`). | `legibility::an unconfigured index selector pulses a next-step hint, no modal tour` | FIXME | (examples §5 — pulsing hint) |

### Principle 6 — AI-first assistant (grounded, cites provenance, never invents a number)

| # | ✅ / ❌ | Test | Status | Retired by |
|---|---|---|---|---|
| 6.1 | ✅ "What am I looking at?" → the assistant answers from the **on-screen** surface (same `analytics.data`, `Market.tsx:49`): names the index, as-of, mode, coverage. ❌ a generic answer that ignores the active surface. | `legibility::the assistant explains the current screen from the on-screen data` | FIXME | (examples §6 / design-doc P6) |
| 6.2 | ✅ The assistant **cites provenance** and never states a number absent from the payload; asked for a figure the screen doesn't hold, it says it can't ground it. ❌ a hallucinated number (anti-pattern #6 — worse than no assistant). Enforce in the **data layer**: the assistant's answer text contains only numbers present in the mocked payload. | `legibility::the assistant never states a number absent from the payload` | FIXME | (design-doc P6 grounding constraint) |
| 6.3 | ✅ The assistant respects the strict/indicative guardrail: it explains indicative mode but never presents an indicative mark as the stored close. ❌ indicative presented as canonical. | `legibility::the assistant explains indicative without presenting it as the stored close` | FIXME | strict-indicative-mode (the load-bearing guardrail) |

### Principle 7 — one design system; spend boldness once

| # | ✅ / ❌ | Test | Status | Retired by |
|---|---|---|---|---|
| 7.1 | ✅ Verdict tone rides the **one** `QcBadge` palette (`qc-badge--{pass\|fail\|unknown}`, `marketHeader.tsx:3-10`) — the status line shows the right badge for the fetch's QC. ❌ a new per-feature accent. | `legibility::the QC verdict renders through the shared QcBadge palette` | LIVE | — |
| 7.2 | ✅ The fabricated-mid guard: a no-quote strike reads `—`, never a synthesized mid (the seam that broke; `market-read-flow.spec.ts:111-112` already asserts it). ❌ a fabricated price filling the gap. | `market-read-flow::… the unquoted put strike reads as an honest gap` | LIVE | — (existing) |
| 7.3 | ✅ One feedback idiom across the app (Market's `AsyncBlock` and Operations' query-state pills converge). ❌ two vocabularies for the same loading/error job (examples §7 "two loading idioms"). | `legibility::loading and error states use one feedback primitive across pages` | FIXME | (examples §7 — one feedback primitive) |
| 7.4 | ✅ The wall-clock helper is shared (one `T(\d{2}:\d{2}:\d{2})` slice). ❌ `fetchTime` (`marketHeader.tsx:15-19`) and `clockTime` (`RunControlPanel.tsx:23-27`) duplicated. *(Unit-level; assert via a render that both show identical clock formatting — e2e can only see the rendered time, so this is a LIVE smoke that the times render, FIXME for the dedupe.)* | `legibility::fetch and job clock times render in one HH:MM:SS format` | LIVE (render) | (examples §7 — shared helper dedupe) |

### Cross-cutting (layout / nav / no-overflow — already covered, listed so the matrix is complete)

| # | ✅ / ❌ | Test | Status |
|---|---|---|---|
| X.1 | ✅ The nav is exactly the seven tabs; `Données/Risque/Ordres/Orders` never appear. ❌ a stale French tab. | `navigation::the nav is exactly the seven tabs and Market is active on load` | LIVE |
| X.2 | ✅ No element collisions / no horizontal overflow at desktop/laptop/narrow. ❌ a control off-screen or panels overlapping. | `layout::[*] *: no element collisions or overflow` | LIVE |
| X.3 | ✅ Every route renders its heading and forwards legacy paths. ❌ a dead route or broken redirect. | `navigation::clicking "*"…` + `navigation::legacy * redirects` | LIVE |
| X.4 | ✅ The full Market read flow (index → nappe → tenor → smile/greeks/price-structure → coverage) renders with no crash. ❌ any step blank or a `pageerror`. | `market-read-flow::Market read flow…` | LIVE |

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **Assertions are on the PM read.** Every test asserts visible text + role/tone (`getByRole`,
  `getByText`, `aria-label`, `role="alert"`/`role="status"`), never internal state — the template is
  `market-read-flow.spec.ts:56-125`. Numeric checks (the 70,7 % fraction) carry a tolerance and a
  hand-derived oracle (`MAT-LEGIBILITY-coverage-headline.md:99-106`), never read back from the component.
- **Danger-state fixtures are the heart of "would they be misled?".** `ANALYTICS_MARKET_CLOSED` drives
  3.3/6.3; `ANALYTICS_PARTIAL_COVERAGE` drives 2b.4/2b.5/3.6/6.1; the existing `ANALYTICS_QUOTED`
  no-quote strike drives 7.2. A fixture that the BFF couldn't emit is a bug — keep them contract-true.
- **`fixme` discipline.** Each `test.fixme` carries (a) the full future assertion, (b) a one-line comment
  citing the MAT-LEGIBILITY spec that retires it (the only `//` allowed — a functional directive, per
  `AGENTS.md:161-165`; everything else is self-evident from the title and assertions). Flipping
  `fixme`→`test` is part of the feature commit, not a follow-up.
- **Determinism / no live BFF.** `page.route` intercepts before the Vite `/api` proxy
  (`playwright.config.ts:7-12`); never let a test reach `127.0.0.1:8000`. `collectPageErrors` on every
  test; assert `pageErrors == []`.
- **How to run.** `cd apps/frontend/web && npx playwright install chromium` (one-time) then
  `E2E_PORT=<free port> npm run e2e`. On this shared host, pick a free `E2E_PORT` so you don't collide
  with another worktree's Vite (`playwright.config.ts:18-22`). Web gate stays
  `npm run lint && npm test`; e2e is the opt-in real-browser layer (`AGENTS.md:106-112`), not the gate.

## Done criteria

`e2e/legibility.spec.ts` exists; every ✅/❌ pair in both design docs is one matrix row here and one named
test in the suite (grep-checkable: each matrix `file::title` resolves to a real test). The **LIVE** rows
pass in a real Chromium against the mocked BFF (`npm run e2e` green, the silent-green canary 3.3's *built*
sibling — the no-quote-mid guard 7.2 and the named-empty-state 3.1 — among them); the **FIXME** rows are
`test.fixme` with their full assertion and the spec that retires them, reported as skipped not failed; the
two danger fixtures (`ANALYTICS_MARKET_CLOSED`, `ANALYTICS_PARTIAL_COVERAGE`) are additive in
`src/test/fixtures.ts`, hand-derived, shared with the component suite; no test touches a live BFF or
`data/`; every test asserts `pageErrors == []`. An agent that later ships a MAT-LEGIBILITY feature flips
its matrix rows from FIXME to LIVE in the same commit and the suite proves the pair.

## Gotchas

- **The running UI is English seven-tab, not French Données/Risque/Ordres.** Assert what *renders today*
  (heading `Market`, `aria-label="Volatility surface"`, figure label `Implied-volatility surface (vol vs
  log-moneyness vs maturity)`). The French strings in the design docs are the **target copy the feature
  specs introduce** — they belong in the `fixme` rows, never in a LIVE assertion against today's code.
  `navigation.spec.ts:21` already forbids the French tabs; don't reintroduce them.
- **Don't assert colour hex.** Tone is a role + words + palette class. `role="alert"` + "marché
  probablement fermé" is the contract; `rgb(220,38,38)` is brittle design-token coupling.
- **Don't fork fixtures.** `ANALYTICS_QUOTED`/`ANALYTICS_AAA` exist; reuse them. Add only the two danger
  fixtures, contract-true, shared with the component suite — one source of truth (Principle 7).
- **Route-override order matters.** `page.route` handlers run most-recent-first; a per-test
  `**/api/analytics**` override after `mockBff(page)` wins — the pattern `market-read-flow.spec.ts:50-54`
  uses. Get this backwards and the danger fixture never loads and the test passes for the wrong reason.
- **A `fixme` is a contract, not a TODO.** Write the real assertion now; the only thing missing is the
  feature. A `fixme` with an empty body is a lie of omission — exactly the silent gap §3 forbids, applied
  to the test suite itself.
- **e2e is opt-in; keep it that way.** Do not wire `npm run e2e` into the shared gate (`AGENTS.md:106-112`)
  — it needs a browser binary + dev server. The contract is: touch a page → run it → keep it green.
