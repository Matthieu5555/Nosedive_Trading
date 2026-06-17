# Frontend design language — concrete examples, grounded in the code we have today

> **Companion to [frontend-design-language-2026].** That doc states the seven principles; this one
> points at the *actual* `apps/frontend/web/src` code (read-only pass, 2026-06-17) and says, for each
> principle: **here is what already does it right — copy this**, and **here is where we fall short —
> here is the concrete fix.** Every claim is anchored to `file:line` so the person building it can open
> the exact spot. The ✅/❌ pairs are the acceptance criteria, not illustrations.
>
> Tone-setter, the owner's own test for every item below: *can the PM tell what they're looking at, and
> would they ever be misled?*

---

## Principle 1 — Progressive disclosure (calm by default, depth on demand)

**✅ Already right — copy this.** The Capture-coverage panel is the one element on Onglet 1 that
discloses instead of shouting: `Market.tsx:215-238` keeps it **collapsed by default** behind a
Show/Hide button driven by `coverageOpen` state (`:36`, `:221-227`), and only mounts `CoveragePanel`
when opened (`:229`). That is exactly the tier-2 ("one interaction away") pattern.

**❌ Where we fall short.** Everything *else* in `market-scroll` (`Market.tsx:115-239`) is a flat,
always-fully-expanded stack — Scorecards, Price, Constituents, the 3D nappe, the Tenor smile+greeks,
Dispersion — all rendered at tier-1 simultaneously. The page is one long scroll where every panel
competes for the eye at full size. **Fix:** apply the coverage panel's disclosure pattern to the
secondary panels (Dispersion `:195-213`, the rate diagnostics inside `TenorPanel`, the constituents
detail) — headline visible, body one click away — so the *decision* surfaces (scorecards, nappe, smile)
own the first screen and the diagnostics recede until asked for.

---

## Principle 2 — Legibility & provenance ("what is this / where did this number come from")

**✅ Already right — copy this.**
- `Scorecards.tsx` is the house exemplar of a legible number: every card is **label + value + hint**
  (`:124-126`), a null reads `"—"` not a blank (`volPoints :6-11`, `levelPercent :13-17`), and the band
  carries a plain-language **sign legend** (`:131-136`, *"RV−IV > 0 = vol cheap (buy)"*). It even tells
  you its own provenance softly — `at 3m (3m not captured)` (`:60-63`) and `(signal)` vs `signal not
  recorded` (`:76-78`).
- `format.ts` is the number law: `sciUnit` (`:40-48`) + the `UNITS` vocabulary (`:57-94`) means an
  analytics number reaches the screen **with its unit**, never naked, and `withCurrency` (`:120-127`)
  renders the right currency symbol.
- The status line `Market.tsx:116-120` states *subject · as-of · QC* (`{index} · as of {effectiveAsOf}
  <QcBadge/>`) — a provenance line at the top of the surface.

**❌ Where we fall short.**
- **Provenance is shallow on the as-of.** `Market.tsx:118` prints `as of 2026-06-17` — the *date* but
  not the **close instant**, which for SX5E is 17:30 CET (`sx5e-close-instant-1730-cet`), not midnight,
  not 22:00. A PM reading "as of 2026-06-17" can't tell *which* instant the surface stands on. ✅ →
  `as of 2026-06-17 17:30 CET (close)`.
- **No "where did this come from" on demand.** The scorecard `hint` says *what* a number is, but a PM
  can't see *which fetch / run_id produced it* or *computed by the BFF vs recomputed on the front*. The
  `(signal)` tag (`Scorecards.tsx:77`) is the seed; the full provenance (source, as-of, computed-where)
  belongs on a hover/ⓘ — this is the copy the assistant (P6) will also read.
- **`Metric.tsx` bypasses the number law.** `Metric` (`:1-8`) renders a bare `label`/`value` string with
  **no unit and no provenance** — any number routed through it escapes the `sciUnit`/`UNITS` idiom. ✅ →
  route every quantitative `Metric` value through `sciUnit`, or give `Metric` a `unit`/`hint` prop so it
  can't render a naked number.

---

## Principle 2b — Self-describing components (every label binds to live state)

**This is the one the owner called out: "when you chart something, dynamically adjust the title."**

**✅ Already right — copy this, it's the model.** `charts.tsx` builds chart titles that bind to the
data *and* to its quality:
- `PriceChart :28` → `${data.underlying} — daily price (OHLC candlestick)` — the underlying is in the
  title; change the index and the title changes.
- `SmileChart :272-274` → the title carries the **selected tenor**, plus `" ⚠ degenerate fit"` when
  `surface_slice.degenerate` and `" — N pts flagged"` when points were dropped. The title *tells you the
  fit went bad.*
- `VolSurface :112` / `:164` → appends `⚠ {flaggedNote}` when slices were railed.
- `TenorPanel` even self-describes the *empty* case: `:88-90` → `"{tenor} is not captured for this close
  — no smile or Greeks to show (projection gap)"`.

**❌ Where we fall short — the exact gap the owner pointed at.**
1. **The visible panel `<h2>` headings are static and generic while the data underneath is dynamic.**
   `Market.tsx:171` is a hard-coded `<h2>Volatility nappe</h2>` with a static caption `:173`
   `<span class="status">all maturities</span>`; `:147` `<h2>Price</h2>` + `:149` `index daily OHLC`.
   These say nothing about *which* underlying, *which* date, or *what coverage* — the self-describing
   sentence only exists *inside* the Plotly `<figcaption>` (`Plot.tsx:20-21`), while the heading the eye
   lands on first is a generic noun.
   - ❌ `<h2>Volatility nappe</h2>` · `all maturities`
   - ✅ `<h2>Nappe de volatilité — SX5E</h2>` · caption `clôture 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations`
2. **Even the dynamic figure label omits subject · as-of · mode · coverage.** `SURFACE_LABEL`
   (`charts.tsx:40`) is the *constant* `"Implied-volatility surface (vol vs log-moneyness vs maturity)"`
   — it describes the *kind* of chart but never names SX5E, the date, strict/indicative, or the
   coverage fraction. So switching the index updates `PriceChart`'s title (it interpolates
   `data.underlying`) but **not** the nappe's (it's a constant). Two charts on one page, one
   self-describes and one doesn't.
   - ✅ → make `SURFACE_LABEL` a function of `(underlying, asOf, mode, coverage)`:
     `Nappe — SX5E · 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations` (degenerate →
     `… · indicative — marché probablement fermé`). The flagged-note suffix already proves the pattern;
     extend the same title to carry identity, not just quality.
3. **One state must drive title + caption + coverage headline + axes together** so they can never
   disagree. Today the title, the `status` caption, and (soon) the coverage headline are assembled in
   different places off different props. ✅ → derive them from the single `(index, effectiveAsOf, mode)`
   tuple `Market.tsx` already holds (`:26`, `:46`), passed down once.

**The rule, restated against this code:** a label that doesn't track its data is the same class of
defect as a wrong number. `SURFACE_LABEL` being a constant is that defect in miniature.

---

## Principle 3 — No silent state, ever

**✅ Already right — this is our best-served principle; it's the house standard.** Every chart has an
honest, non-blank empty state instead of a void:
- `PriceChart :29-36` (*"No daily bars for {underlying} in this window."*), `VolSurface :165-172`
  (*"No surface to plot yet."*), `SmileChart :257-264` and `:296-303`, `GreeksShapeCurves :330-355`,
  `TenorPanel :86-91` (the projection-gap `role="status"`).
- `GlobalErrorBanner.tsx` is the root failure surface (`:21` `role="alert" aria-live="assertive"`) that
  makes "no silent failure" true; `AsyncBlock :17-23` renders errors as `role="alert"`. (This is the
  `frontend-no-silent-failures` doctrine, already enforced.)

**❌ Where we fall short.**
- **Loading is the weak link — it's a bare text, not a skeleton.** `AsyncBlock :10-14` renders the
  literal `"Loading…"` in a one-line `state-panel`, so a 480px-tall nappe pops in from a single line of
  text and the layout reflows on every fetch. The research is unambiguous: a skeleton of the chart's
  footprint reads ~30% faster and stops the reflow. ✅ → an `AsyncBlock` (or a `<ChartSkeleton>`) that
  reserves the panel's real height while loading.
- **Empty copy doesn't name its subject.** *"No surface to plot yet"* (`VolSurface :169`) is honest but
  generic; §2b says the empty state self-describes too. ✅ → *"Aucune nappe pour SX5E au 2026-06-17 —
  marché probablement fermé."*

---

## Principle 4 — Every action explains itself; long processes narrate

**✅ Already right — copy this.** `RunControlPanel` is the strongest action-feedback surface we have:
the launch button flips `{launch.isPending ? "Launching…" : "Launch run"}` (`:166`) and disables while
pending (`canLaunch :117`); `JobsTable` shows a live ledger of state pills queued/running/done/error
(`JOB_STATE_CLASS :16-21`, `JobRow :40-53`) with started/finished/message; the empty case is an
honest *"No runs launched this session yet…"* (`:56-60`); a failed launch is a labelled
`role="alert"` (`:174-178`).

**❌ Where we fall short.**
- **No progress for the long job — only a coarse state flip.** A capture is a 10s-to-minutes job, but
  the only feedback is the button reverting to idle immediately and a job row flipping
  queued→running→done (`:166`, `:48-49`). There is **no determinate %, no step narration** ("solving
  IV, 1 706 points…"), no ETA — exactly the 10s+ case Principle 4 calls for. ✅ → a determinate /
  step-based progress on the running job (the engine already knows the stages).
- **The button doesn't say what it does underneath.** "Launch run" kicks a real provider capture, but
  there's no hover/gloss saying *what backend action fires*. ✅ → a one-line gloss: *"Fetches a fresh
  option chain from {provider} and rebuilds the surface — writes a new capture run."*
- **Onglet-1 selectors refetch silently.** Changing index/as-of (`Market.tsx:80-84`, `:92-96`) triggers
  a backend fetch with no feedback beyond the panel swapping to `"Loading…"`. ✅ → tie the selector to a
  visible "fetching {index} {date}…" affordance.

---

## Principle 5 — Contextual guidance that points, flashes, gets out of the way

**✅ Already right — the seed exists.** We already write good *always-on inline micro-glosses*:
`ConvexityReadout` carries its own formula hint (`TenorPanel.tsx:27-29`, *"butterfly: IV(25Δp) +
IV(25Δc) − 2·ATM (vp = vol point = 0.01 IV)"*); the Scorecards sign legend (`:131-136`); each
scorecard's `hint` (`:69`, `:92`). These are the right instinct — explain in place, in PM language.

**❌ Where we fall short.** There is **zero interactive contextual help** — no ⓘ hotspots, no hover
tooltips, no spotlight/mask, no pulsing next-step hint, no onboarding. A first-time PM gets no
"what is this / how do I" affordance; the glosses above are hardcoded prose buried in each component,
not a reusable hover. ✅ → a single `<InfoDot>`/tooltip primitive (the tier-2 carrier) that any element
can hang an explanation on, plus a one-time pulsing hint on the index selector when the page first loads
with nothing chosen (`Market.tsx:26` `index === ""`).

---

## Principle 6 — AI-first assistant (the biggest greenfield, and the highest leverage)

**❌ Nothing exists yet — but the raw material is already in the code.** The "what is this / how to read
it" copy is *already written*, just scattered as inline constants: `SURFACE_LABEL` (`charts.tsx:40`),
`SMILE_HEAD` (`:231`), `GREEKS_SHAPE_HEAD` (`:307`), the convexity gloss (`TenorPanel.tsx:28-29`), every
scorecard `hint` (`Scorecards.tsx`). 

✅ **First step (cheap, unblocks both P5 and P6):** lift those strings into one **explanation map** keyed
by element id — the single home for "what is this / how to read it / where it comes from." The ⓘ
tooltip (P5) and the assistant (P6) both read it, so the copy is written once and can never diverge from
what the tooltip shows. The assistant then answers "what am I looking at?" by reading the same
`analytics.data` the page already holds (`Market.tsx:49`) plus that map, and **cites provenance** rather
than inventing a number — and it must respect the strict/indicative guardrail
([MAT-LEGIBILITY-strict-indicative-mode]): explain indicative, never present it as the stored close.

---

## Principle 7 — One design system; spend boldness once

**✅ Already right.** `QcBadge` (`marketHeader.tsx:3-10`) is the shared verdict-tone primitive; chart
colour rides `CHART_COLORS`/`chartTheme` and the `--negative`/`--positive` tokens
(`charts.tsx:228-229`, `Scorecards signColor :22-26`); numbers ride `format.ts`. The vocabulary is real
and reused.

**❌ Where it's drifting.**
- **Two loading/feedback idioms.** Market-side panels use `AsyncBlock` + the bare `"Loading…"` /
  `state-panel-error` (`AsyncBlock.tsx`), while `RunControlPanel` uses react-query `isPending`/`isError`
  + `ops-pill` state classes (`:16-21`). Two vocabularies for the same job. ✅ → one feedback primitive.
- **Duplicated clock-time helper.** `fetchTime` (`marketHeader.tsx:15-19`) and `clockTime`
  (`RunControlPanel.tsx:23-27`) are the *same* `T(\d{2}:\d{2}:\d{2})` slice, copied. ✅ → one shared
  helper in `lib/format.ts`.
- **`Metric.tsx` is an off-system number** (see P2) — a label/value with no unit, the one place a naked
  number can reach the screen.

---

## The shortlist (if we build in this order)

1. **§2b self-describing titles** — make `SURFACE_LABEL` (and the panel `<h2>`s) functions of
   `(underlying, as-of, mode, coverage)`. Cheapest, highest "know wtf is going on" return; pairs with the
   coverage headline already specced in [MAT-LEGIBILITY-coverage-headline].
2. **Skeletons** — replace the bare `"Loading…"` in `AsyncBlock` with footprint-preserving skeletons (P3/P4).
3. **The explanation map + `<InfoDot>`** — centralize the scattered head/hint copy (unblocks P5 and P6 at once).
4. **Job progress narration** — determinate/step progress on the capture run (P4).
5. **The assistant** — reads `analytics.data` + the explanation map, cites provenance (P6). The flagship.

Items 1–3 are pure surfacing on data we already hold (the cheap, safe wins); 4–5 are the new surfaces
the owner is reaching for.
</content>
