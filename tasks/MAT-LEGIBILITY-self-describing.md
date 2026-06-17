# MAT-LEGIBILITY-self-describing — every title, axis, legend, caption and tooltip is a sentence the data writes

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on" — and
> verbatim, a year on, *"quand tu graphes quelque chose, ajuste dynamiquement le titre"* /
> *"Qu'est-ce que je suis en train de regarder ? C'est pas clair"* (`Conseils-front-end:46`). Today the
> nappe panel reads `<h2>Volatility nappe</h2>` with a static caption `all maturities`
> (`pages/Market.tsx:171`, `:173`), and the Plotly figure label is the **constant** `SURFACE_LABEL`
> (`components/charts.tsx:40`) — *"Implied-volatility surface (vol vs log-moneyness vs maturity)"*. That
> sentence is true of every surface ever drawn: it never names SX5E, never names the date, never names
> the close instant, never says strict-or-indicative, never says coverage. Switch the index and the
> price chart's title rewrites itself (`charts.tsx:28` interpolates `data.underlying`) but the nappe's
> does not. **Two charts on one page, one self-describes and one lies by omission.** This is
> Principle 2b made concrete: a label that does not track its data is the same class of defect as a
> wrong number.

## What's true today (grounded in code)

- **The good pattern already exists, scattered.** `PriceChart` binds its label to data
  (`charts.tsx:28` → `${data.underlying} — daily price (OHLC candlestick)`). `SmileChart` carries the
  selected tenor *and* the fit quality (`charts.tsx:272-274` → `Smile — {label} (…)` + `" ⚠ degenerate
  fit"` + `" — N pts flagged"`). `VolSurface` appends `⚠ {note}` when slices were railed
  (`charts.tsx:112`, `:164`). `TenorPanel` self-describes even the empty case (`TenorPanel.tsx:88-90`
  → `"{tenor} is not captured for this close — no smile or Greeks to show (projection gap)"`). These
  prove the house already knows how to do this; the gap is that it is done *per chart, off different
  props, in English*, never off one shared state tuple, and the **first thing the eye lands on — the
  panel `<h2>`/caption — is static** (`Market.tsx:147-149`, `:169-173`, `:197-201`).
- **The figure label is a single string prop.** Every chart renders through `Plot`
  (`Plot.tsx:18-21`): `<figure aria-label={label}><figcaption>{label}</figcaption>`. One `label`
  prop drives both the accessible name and the visible caption — so whatever sentence we compute is
  rendered identically to sighted and screen-reader users. There is no second place a title can drift.
- **The state that should drive the sentence is all already in `Market.tsx`.** The index
  (`Market.tsx:26`), the resolved as-of (`effectiveAsOf`, `:46`), the QC verdict (`:113`), and the
  surface payload (`analytics.data.surface`, `:179`) are held in one component. A `mode` state
  (strict|indicative) is being added by [MAT-LEGIBILITY-strict-indicative-mode]; a `coverage` block is
  being added to `/api/analytics` by [MAT-LEGIBILITY-coverage-headline]. The four facts of the sentence
  — **subject · as-of · mode · coverage** — converge here.
- **The axes are already unit-labelled, but not in the house idiom or PM register.** `charts.tsx`
  axis titles are `log-moneyness` / `maturity (years)` / `implied vol` (`:117-119`, `:207-214`),
  `strike` (`:310`), `log-moneyness (k)` / `implied vol` (`:236-237`). They carry a unit word but in
  engine English, and they do **not** route through `lib/format.ts` `UNITS`. The legend names real
  series (`puts`/`calls` `charts.tsx:294-295`, `delta`/`gamma`/`vega` `:362-386`, `ATM` `:71`) — that
  part is already right; do not regress it.
- **The as-of is shallow.** `Market.tsx:118` prints `as of {effectiveAsOf}` — the *date* only. For
  SX5E the close instant is **17:30 CET (OESX settlement)**, not midnight and not the 22:00 XEUR
  futures close (`sx5e-close-instant-1730-cet`). "as of 2026-06-17" cannot tell a PM which instant the
  surface stands on.

## Objective

**One piece of state writes the sentence, and the sentence is everywhere the screen names itself.**
Introduce a single derived descriptor for the active surface and feed it to the panel heading, the
panel caption, the Plotly figure label, the empty/error copy, and the data-point tooltip — so they can
**never disagree**. The descriptor binds to live state and re-renders in the same paint as the chart:
flip the index selector and the `<h2>`, the caption, the nappe title, the smile title, and the coverage
headline all update together.

The canonical title sentence (the normative target from `frontend-design-language-2026.md:99-102`):

> ✅ `Nappe de volatilité — SX5E · clôture 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations`
> ✅ (indicative) `Nappe de volatilité — SX5E · 2026-06-17 · INDICATIF · 2 280/2 412 (574 marques indicatives)`
> ✅ (degenerate) `Nappe de volatilité — SX5E · 2026-06-17 · indicative — marché probablement fermé`
> ❌ `Nappe de volatilité` — true of every surface ever; tells the PM nothing.
> ❌ `Volatility nappe` / `all maturities` — today's static heading; names neither subject nor instant.

This is **surfacing, not new compute.** The four facts already exist (subject, as-of, mode, coverage);
this task assembles them into one descriptor and renders it consistently. It depends on the coverage
block and the mode state, but degrades cleanly when either is absent (see Gotchas).

## Design intent (this is a designed element, not a status string)

The locked Onglet-1 reading model (`frontend-page1-reading-model`) and the design-system vocabulary
(`QcBadge`, `lib/format.ts`) are **not** to be reinvented. The descriptor reads like part of the page,
in the existing type/colour system, answering the page's own *"qu'est-ce que je regarde ?"*.

- **One source of truth, four consumers.** A single `describeSurface(state)` helper takes
  `{ underlying, asOf, closeInstant, mode, coverage }` and returns the structured descriptor
  (`subject`, `asOf` string, `mode`, `coveragePhrase`, `tone`). The panel `<h2>`, the panel caption,
  the `Plot` `label`, the empty-state copy, and (where shown) the point tooltip all read **the same
  object**. There is no second place a title is assembled. This is the §2b rule made literal: if two
  labels on the same screen can contradict, the screen is broken even if every pixel renders.
- **Subject · as-of · mode · coverage — in that order.** The subject is the underlying symbol (and
  the chart kind in the figure caption, not the heading). The as-of carries the **close instant**:
  `clôture 2026-06-17 17:30 CET` for SX5E (resolve the instant from the same calendar source as the
  backend; never hard-code 22:00). Mode is the word `strict` (quiet) or `INDICATIF` (warning tone,
  boldface — the badge spec owns the visual badge; this owns the *word in the sentence*). Coverage is
  the headline phrase `1 706/2 412 cotations` (strict) or `2 280/2 412 (574 marques indicatives)`
  (indicative), sharing the **one** coverage metric from [MAT-LEGIBILITY-coverage-headline] — do not
  recompute a second fraction.
- **State, not chrome — reuse the three tones.** The descriptor's `tone` keys off the same
  full/partial/degenerate + strict/indicative states the coverage headline and the indicative badge
  already define, on the existing `QcBadge` palette. No new accent. The title recedes (neutral) when
  coverage is full and strict; it raises its voice (warning) when indicative is active or coverage is
  partial; it goes loud (error tone + plain words `marché probablement fermé`) on the degenerate close.
- **Plain words, PM register** (`analytics-pm-legible-framing`): `Nappe de volatilité`, `cotations`,
  `marques indicatives`, `clôture` — never `Volatility nappe`, `IV points`, `snapshots`, `quarantined`.
  The figure caption may keep the one-line *how-to-read* gloss (`charts.tsx:40`, `:231`, `:307`) but the
  **identity** (subject·as-of·mode·coverage) leads.
- **Axes carry their unit in the house idiom.** Every axis title is `label (unit)` where the unit
  comes from `lib/format.ts` `UNITS` (`format.ts:57-94`) — `log-moneyness (ln(K/F))`,
  `maturité (y)`, `vol implicite (Vol)`, `strike ($)` re-currencied via `withCurrency`
  (`format.ts:120-127`). An unlabeled or unitless axis is a bug. (Keep the existing trader-unit
  *tick formats* — `.2f`, `.0%`, `.2s` — they are correct; this is about the **title** carrying the
  unit, not the ticks.)
- **Legend names the actual series** — already true (`puts`/`calls`/`ATM`/`delta`/`gamma`/`vega`);
  the rule is *do not regress it*. If a series is dropped, it leaves the legend; if a point is
  indicative, its tooltip says so (below).
- **Empty/error copy names the subject.** Not `No surface to plot yet` (`charts.tsx:169`) but
  `Aucune nappe pour SX5E au 2026-06-17 — marché probablement fermé.` The empty state self-describes
  off the *same* descriptor as the populated state.
- **Data-point tooltip shows real coordinates + provenance.** A hovered nappe/smile point reads
  `strike 4 200 · 1m · IV 18,3 % · deux-faces` (strict / `observed_two_sided`) vs
  `… · marque indicative à une face` (indicative / `one_sided`|`last`) — Principle 2 at the pixel,
  reading the per-point provenance taxonomy that [MAT-LEGIBILITY-strict-indicative-mode] attaches.
- **Never silently wrong, never silently absent.** If `coverage` is missing from the payload, the
  sentence omits the coverage clause and says `couverture indisponible` rather than printing a fraction
  it doesn't have. If `mode` isn't wired yet, default `strict` and omit the badge word — never invent
  `indicative`.

## Owns

- **Front — the descriptor + its consumers (the core of this task).**
  - A `describeSurface(state)` helper (new, e.g. `lib/surfaceDescriptor.ts`) returning the structured
    descriptor `{ subject, asOf, mode, coveragePhrase, tone, emptyCopy }`. Pure, unit-tested.
  - A `closeInstant(underlying, asOf)` helper that resolves the close time-of-day (SX5E → 17:30 CET)
    from the index registry the BFF already exposes — **not** a hard-coded constant, and **not** 22:00.
    If the instant is unknown, render the date alone (omit the time), never a wrong time.
  - Rewire `Market.tsx` so the nappe panel `<h2>` (`:171`) and caption (`:173`) read the descriptor;
    extend the same to the Price (`:147-149`) and Dispersion (`:199-201`) headings for consistency.
  - Make `SURFACE_LABEL` (`charts.tsx:40`) a **function** of the descriptor (or pass the descriptor's
    title into `VolSurface`/`SmileChart`/`GreeksShapeCurves` as a `title` prop) so the figure caption
    carries subject·as-of·mode·coverage, preserving the existing `⚠ {note}` flagged-fit suffix.
  - Self-describing empty copy in `VolSurface` (`charts.tsx:165-172`) and `SmileChart`
    (`charts.tsx:257-264`, `:296-303`) off the descriptor.
  - Axis titles routed through `UNITS`/`withCurrency` in `charts.tsx` `*_LAYOUT`/`scene` blocks.
  - Point tooltip (Plotly `hovertemplate` / `text`) carrying coordinates + provenance.
- **No BFF change of its own** — it *consumes* the `coverage` block ([MAT-LEGIBILITY-coverage-headline])
  and the `mode`/per-point provenance ([MAT-LEGIBILITY-strict-indicative-mode]). If those aren't landed
  yet, build against their typed contracts and degrade (see Gotchas). The one thing it may need from the
  BFF is the **close instant / timezone** on the index registry payload if not already present — if
  absent, add it as an additive-nullable field rather than hard-coding.
- Tests on both the pure descriptor and the rendered components.

## Depends on / coordinates with

- **Sibling of the two other legibility specs and shares their contracts.** The coverage clause is the
  [MAT-LEGIBILITY-coverage-headline] metric (one fraction, computed once in the BFF — do **not** fork
  it); the `mode` word and per-point provenance are [MAT-LEGIBILITY-strict-indicative-mode]'s. Land
  those first where possible; this task can be built against their typed stubs and is the element that
  ties the *headline + badge + title* into one consistent sentence.
- **Frontend is owner-owned** (`frontend-is-owner-owned`): `apps/frontend/web` is Matthieu's lane.
  Any fleet contribution here is additive-read-only on the BFF (the close-instant field) plus a spec;
  the React edits are the owner's to apply or to explicitly delegate. Claim the row on
  `tasks/TASKBOARD.md` and coordinate the `Market.tsx` heading edits with the live front lane (they
  overlap the same `Market.tsx:147-201` block the coverage-headline mount touches).
- **Surfacing, not recompute.** All four facts exist; this assembles them.

## What to do (ordered)

1. **`describeSurface` (pure, no React).** Input `{ underlying, asOf, closeInstant, mode, coverage }`;
   output the structured descriptor + `tone` (`full`|`partial`|`degenerate`) + PM-register strings.
   Encodes the exact sentence: subject · `clôture {asOf} {HH:MM} {tz}` (or bare date if instant
   unknown) · `strict`/`INDICATIF` · coverage phrase. Degenerate → the `marché probablement fermé`
   copy in error tone. Missing coverage → `couverture indisponible`, never a fabricated fraction.
   Unit-test it against a hand-built oracle (below) before wiring any component.
2. **`closeInstant` helper.** Resolve the close time-of-day from the index registry (SX5E → 17:30
   CET); unknown → null → date-only render. Never 22:00, never a hard-coded literal in the component.
3. **Wire the figure label.** Turn `SURFACE_LABEL` into a function of the descriptor (or thread a
   `title` prop), keeping the `⚠ {flaggedNote}` suffix (`charts.tsx:93`, `:163`). Same for the smile
   and greeks titles so the whole nappe block sings one sentence.
4. **Wire the panel headings + captions** in `Market.tsx` (`:147-149`, `:169-173`, `:197-201`) off the
   same descriptor — the heading the eye lands on first stops being a generic noun.
5. **Self-describing empty/error copy** in `VolSurface`/`SmileChart`, off the descriptor's `emptyCopy`.
6. **Unit the axes** through `UNITS`/`withCurrency`; keep the trader-unit tick formats.
7. **Point tooltip** with coordinates + provenance (reads the per-point provenance from
   [MAT-LEGIBILITY-strict-indicative-mode]; in strict-only payloads every point is `deux-faces`).
8. **No look-ahead.** The descriptor reads only the requested/resolved date's state; the close instant
   is the *requested* date's instant, never a later one. (`check-lookahead-bias` clean.)

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values derived from a source other than the code
under test.

- **`describeSurface` — hand-built oracle (unit).** For each state, assert the exact string:
  - strict + full → `Nappe de volatilité — SX5E · clôture 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations`, tone `full`.
  - indicative + partial → `… · INDICATIF · 2 280/2 412 (574 marques indicatives)`, tone `partial`.
  - degenerate (0 two-sided) → `… · indicative — marché probablement fermé`, tone `degenerate`.
  - coverage `null` → sentence ends `couverture indisponible`; no fraction printed.
  - close instant unknown → `clôture 2026-06-17` (date only); **never** `22:00`.
- **One state drives all labels (component test).** Render the nappe panel; assert the `<h2>`, the
  panel caption, and the Plotly figure `aria-label`/`figcaption` (`Plot.tsx:20-21`) **all** contain the
  same subject·as-of·mode·coverage substring. Then change the `index` prop and assert **all** of them
  changed together — the regression that catches a title drifting from its data.
- **No two labels contradict.** With an indicative + partial payload, assert no rendered label says
  `strict` and none says `couverture complète`; the mode word and the coverage phrase agree.
- **Axes carry units (component test).** Assert each axis title contains its `UNITS` token
  (`log-moneyness (ln(K/F))`, `vol implicite (Vol)`, `strike (€)` for an EUR index via `withCurrency`).
- **Empty state self-describes.** An empty surface payload renders
  `Aucune nappe pour SX5E au 2026-06-17 …`, not the generic `No surface to plot yet` — and reads
  differently from the error state (`role="alert"`), per Principle 3.
- **Tooltip provenance.** A one-sided/indicative point's tooltip text contains `marque indicative`; a
  two-sided point's contains `deux-faces`.
- **No look-ahead.** Resolving the descriptor for past date D never reads a later date's close instant
  or coverage (inject one; assert unchanged). `check-lookahead-bias` clean.
- Gate green: the web suite (`tsc + lint + vitest + playwright` — `apps/frontend/README.md`); if the
  close-instant BFF field is touched, the root gate
  `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.

## Done criteria

A single `describeSurface(state)` is the only place the nappe's identity sentence is assembled, and the
panel `<h2>`, the panel caption, the Plotly figure label, the empty/error copy, and the point tooltip
all read it — so they cannot disagree. Switching the index (or the as-of, or the mode) rewrites
**all** of them in the same paint. The sentence carries **subject · as-of (with the 17:30 CET close
instant) · mode (strict/INDICATIF) · coverage**, in PM French, on the existing `QcBadge` tone palette
with no new accent; degrades to `couverture indisponible` and to a date-only as-of without ever
printing a wrong or invented value. Axes carry their `UNITS` unit; the legend still names real series;
the empty state names its subject and reads differently from the error state. No look-ahead; the web
gate is green. A 2026-06-17-style 30%-excluded day, an indicative recompute, and a market-closed
degenerate close are each **obvious from the title alone**.

## Gotchas

- **One sentence, one source.** The entire point is a single descriptor feeding every label. If you
  assemble the title in `charts.tsx` and the caption in `Market.tsx` off different props, you have
  rebuilt the bug. Derive once, pass down once.
- **Don't fork the coverage metric.** The coverage clause is the [MAT-LEGIBILITY-coverage-headline]
  fraction, computed once in the BFF. Read it; never recompute a second one here.
- **The close instant is 17:30 CET, resolved — not 22:00, not hard-coded** (`sx5e-close-instant-1730-cet`).
  Wrong instant on the title is exactly the kind of confident lie this task exists to kill. Unknown
  instant → omit the time, never guess.
- **Degrade, never invent.** No mode wired → `strict`, no badge word. No coverage → `couverture
  indisponible`. No close instant → date only. The label must be unable to be false for its contents.
- **Don't regress the legend or the tick formats.** Series names and trader-unit ticks (`.2f`/`.0%`/
  `.2s`) are already right; this task adds the *unit to the axis title* and the *identity to the
  caption* — it does not touch the ticks or rename the series.
- **Don't reinvent the design system.** Reuse `QcBadge` tones + `lib/format.ts`; the locked reading
  model wins. The boldness is spent on the indicative word and the degenerate copy, nowhere else — a
  title that shouts on every render trains the eye to ignore the one render that matters.
- **Owner's lane.** `apps/frontend/web` React edits are Matthieu's; ship the spec + any additive BFF
  field, and coordinate the `Market.tsx` heading edits rather than landing them unannounced.
