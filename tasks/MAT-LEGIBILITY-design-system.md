# MAT-LEGIBILITY-design-system — one design system, one token source, spend boldness once

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on." That promise
> only holds if the *surface itself* is coherent: one palette, one number idiom, one verdict-tone badge,
> one feedback pattern. The moment a second accent or a second "loading…" idiom appears, the eye stops
> trusting any of them — and a banner that shouts everywhere is a banner the PM learns to ignore. This is
> Principle 7 of [frontend-design-language-2026] made into a build task: **reuse, don't reinvent; spend
> boldness in exactly one place per screen; in fintech, trust *is* the product.** It is the discipline
> that lets the other six principles (and the three sibling MAT-LEGIBILITY specs) cohere instead of
> sprawl. No new look — this *removes* divergence, it does not add a theme.

> **Cross-spec reconciliation (canonical — see [MAT-LEGIBILITY-build-order]).** The **skeleton loading
> state** of `AsyncBlock` is **owned by [MAT-LEGIBILITY-skeletons]** (the `<ChartSkeleton>` primitive + the
> loading-branch swap). This spec **consumes** that one primitive for its "one feedback idiom" consolidation
> — it does **not** build a second skeleton; it routes the ops-side fetch loading/error through the *same*
> `AsyncBlock`/`<ChartSkeleton>`. Likewise, the shared **explanation map** that the legibility cluster
> converges on is the canonical `lib/explain.ts` ([MAT-LEGIBILITY-explanation-map]); if this spec's `ui/` ADR
> decides the home for shared primitives, `InfoDot`/the map build on that decision, they don't predate it.
> What this spec uniquely owns: the `:root`-token derivation in `chartTheme.ts`, the single `QcBadge`, the
> single `clockTime`, the single `Callout`, the `Metric` unit fix, and the `ui/` ADR.

## What's true today (grounded in code, read-only pass 2026-06-17)

The vocabulary is real and mostly single-sourced — and it has started to fork in three concrete places.

- **The token source of truth is genuinely single — keep it that way.** `index.css:40-55` defines the
  palette as `:root` CSS vars (`--bg`, `--panel`, `--positive`, `--negative`, `--amber`, `--blue`,
  `--radius`); `index.css:24-38` (`@theme inline`) maps every Tailwind colour token onto those same vars,
  so a utility like `bg-panel` / `text-muted` and a legacy rule like `.panel { background: var(--panel) }`
  resolve to the **same** hex. Change one `:root` var and both move. This is the asset to protect.
- **But the chart layer hand-copies the palette instead of reading it.** `chartTheme.ts:4-17`
  (`CHART_COLORS`) re-declares `#7fd99a`/`#f08a7e`/`#e8c264`/`#79b8d6` as literals, with a comment that
  says "kept byte-identical to the index.css `--positive`/… tokens" (`:9-11`). `CHART_FONT_FAMILY`
  (`:23-24`) does the same for the font stack ("keep these two in lockstep", `:22`). Two files, one
  palette, held in sync **by a comment and good intentions** — exactly the drift this spec exists to stop.
- **A real shadcn layer exists and is wired to those tokens — but is imported by nothing.** `ui/badge.tsx`,
  `ui/button.tsx`, `ui/card.tsx`, `ui/dialog.tsx`, `ui/input.tsx`, `ui/label.tsx`, `ui/select.tsx`,
  `ui/tabs.tsx` are present, built on `cva` + `cn` (`lib/utils.ts:4-6`) and use the `border-positive` /
  `bg-panel` token utilities (`ui/badge.tsx:11-16`, `ui/button.tsx:11-17`), so they already speak the
  exact `:root` palette. `components.json` registers them (`"ui": "@/ui"`, `style: "new-york"`). **Measured:
  zero files in `src/` import any of them** (`grep` for `@/ui`/`../ui` returns nothing) — the app's live
  surface is still hand-rolled CSS classes (`.qc-badge`, `.ops-pill`, `.state-panel`, `.metric`). So we
  have *two* component vocabularies sitting side by side: one in use, one dormant.
- **Three duplicated copies of the same idioms have already forked:**
  1. **Two `QcBadge` definitions.** `marketHeader.tsx:3-10` and `FreshnessPanel.tsx:5-12` are the *same*
     component, copied. They render the same `.qc-badge--{qc}` markup — but the copies can drift, and
     a verdict tone is load-bearing.
  2. **Three time-of-day slicers.** `fetchTime` (`marketHeader.tsx:15-19`), a *different* `fetchTime`
     (`FreshnessPanel.tsx:14-18`, returns `"time unknown"` not `null`), and `clockTime`
     (`RunControlPanel.tsx:23-27`) are all the same `T(\d{2}:\d{2}:\d{2})` slice with three different
     null-fallbacks. The fallback divergence is a legibility bug: one says nothing, one says "time unknown".
  3. **Two loading/feedback idioms.** Market-side panels use `AsyncBlock` + bare `"Loading…"`
     (`AsyncBlock.tsx:10-14`) and `.state-panel-error` (`:17-23`); ops-side uses react-query
     `isPending`/`isError` (`RunControlPanel.tsx:117,128,166,181`) + `.ops-pill` state classes
     (`:16-21,44`). Two vocabularies for "this is loading / this failed".
- **`Metric.tsx:1-8` is the one off-system number** — a bare `label`/`value` with **no unit, no
  provenance**, so any number routed through it escapes the `sciUnit`/`UNITS` law (`format.ts:40-94`).
  `FreshnessPanel.tsx:49` already routes a *time* through it; the risk is a quantitative value following.
- **"Spend boldness once" is observed in some places, violated by design pressure in others.** The
  scorecard band is correctly quiet — a "whisper-faint" gradient (`index.css:701-725`) with the *value*
  as the one bold thing per card (`.scorecard__value`, `index.css:760-765`). But every new feature wants
  its own callout box: `.gaps` amber (`index.css:1259-1273`), `.ops-backlog` amber (`:1447-1456`),
  `.accepted-banner` (`:1084-1092`) — three near-identical amber-left-border callouts. Each is fine alone;
  together they are three accents doing one job.

## Objective

A single enforced design system, so that **a PM can never be misled by an inconsistent surface** — the
same verdict reads the same tone everywhere, the same number carries its unit everywhere, the same
"loading" looks the same everywhere, and exactly one element per screen earns emphasis. Concretely:

1. **One token source.** The `:root` vars (`index.css:40-55`) are the only place a colour/radius/font is
   defined. `chartTheme.ts` *reads* them at runtime instead of re-declaring literals — the "byte-identical"
   comment becomes an unnecessary comment because divergence is no longer possible.
2. **One of each shared primitive.** One `QcBadge`, one time-of-day formatter, one feedback primitive,
   one callout. The dormant `ui/` shadcn layer is either adopted as the home for these or explicitly
   parked with an ADR — it must not sit as a silent second vocabulary.
3. **No off-system number.** `Metric` cannot render a naked quantitative value.
4. **One bold thing per screen.** A documented, enforced rule: each surface nominates one element that
   earns emphasis (the coverage headline when coverage is low; the INDICATIF badge when indicative is on;
   the scorecard value); everything else recedes to the muted/neutral palette.

This is the umbrella that the three sibling specs ride: [MAT-LEGIBILITY-coverage-headline] (the headline
must reuse `QcBadge` tones, not a new accent), [MAT-LEGIBILITY-quarantine-drilldown] (the disclosure must
reuse the one disclosure pattern), and [MAT-LEGIBILITY-strict-indicative-mode] (the INDICATIF badge is the
one bold thing when active). None of them may introduce a fourth amber or a second loading idiom — this
spec is what they conform to.

## Design intent (this is consolidation, not a redesign)

- **Remove divergence; add nothing visible.** The end state must look **pixel-identical** to today on a
  full-coverage, healthy day. The win is structural: fewer sources of truth, so the surface *can't* drift.
  If a screenshot diff shows a visual change, that is a regression unless the spec explicitly called for it.
- **The token is the law, the comment is not.** A palette held in sync by a `// keep in lockstep` comment
  is a defect waiting to happen (`.agent/conventions.md`: the *why* lives in `.agent`, not inline; a
  comment that asserts an invariant the code doesn't enforce is the same lie class as a stale log line).
  Replace the comment-enforced invariant with a code-enforced one.
- **Plain words, PM register** (`analytics-pm-legible-framing`): the consolidation must not regress any
  user-facing copy. "QC pass", "Launching…", "deux-faces" stay exactly as they read today.
- **One feedback grammar.** Loading is a skeleton or a labelled `role="status"`; failure is a loud
  `role="alert"` in the error tone; both read **differently** from each other and from a populated panel
  (`frontend-no-silent-failures`, Principle 3). After this task there is one way to express each, not two.
- **Spend boldness once, per `.agent/voice.md` discipline applied to pixels.** The boldness budget per
  screen is one. The three amber callouts collapse to one `Callout` primitive with a tone prop; whether a
  given screen *uses* amber is a per-screen decision, not a per-feature reflex.

## Owns

- **Front only.** No BFF change. No new endpoint, no payload change. This is a `src/` refactor +
  one ADR.
- **`chartTheme.ts`** — derive `CHART_COLORS`/`CHART_FONT_FAMILY` from the `:root` tokens (read CSS
  custom properties via `getComputedStyle(document.documentElement)` at module init, or a tiny
  `tokens.ts` that both `index.css`-consumers and the chart theme import). Delete the "byte-identical"
  comments once the value is derived, not copied.
- **One `QcBadge`** in a shared module (e.g. `components/QcBadge.tsx`), imported by both
  `marketHeader.tsx` and `FreshnessPanel.tsx`; delete the duplicate.
- **One time-of-day formatter** in `lib/format.ts` (e.g. `clockTime(ts, { fallback })`), consumed by
  `marketHeader.tsx`, `FreshnessPanel.tsx`, `RunControlPanel.tsx`; delete the three copies. Pick **one**
  null-fallback policy and document it (recommend: return `null`, let the caller omit — the
  `marketHeader` policy — so no surface prints a placeholder string).
- **One feedback primitive** — `AsyncBlock` becomes the single carrier. The skeleton loading state it
  carries is **[MAT-LEGIBILITY-skeletons]' `<ChartSkeleton>`** (consume it, do not build a second); this
  spec's slice is routing the ops panels through that *same* `AsyncBlock` in place of their bespoke
  `isPending`/`isError` rendering, **or** unifying the two idioms behind one wrapper.
  The `.ops-pill` *state ledger* (queued/running/done/error) is a legitimately different thing (a job
  history, not a fetch state) and stays — this is about the *fetch* loading/error idiom, not the job pills.
- **`Metric.tsx`** — give it a required `unit?` / `hint?` prop and route quantitative values through
  `sciUnit`, so a naked number can't reach the screen (examples doc §P2 ✅).
- **One `Callout` primitive** — fold `.gaps`, `.ops-backlog`, `.accepted-banner` into one component with
  a `tone` ("info" | "warn" | "ok") prop off the existing palette; delete the duplicated CSS blocks.
- **An ADR** in `.agent/decisions/` recording the shadcn-`ui/` decision (adopt as the primitive home, or
  park with a reason) so the next agent doesn't rediscover a dormant second vocabulary and guess.
- Tests for every consolidated primitive.

## Depends on / coordinates with

- **`Market.tsx` is owner-owned** (`frontend-is-owner-owned`): the page headings/mount points are
  Matthieu's lane. This spec touches **shared primitives** (`lib/format.ts`, `components/QcBadge.tsx`,
  `components/Metric.tsx`, `chartTheme.ts`, `AsyncBlock.tsx`) and the *callers* of the dupes — coordinate
  any edit inside `pages/Market.tsx`/`pages/market/*` on the TASKBOARD before touching it, and prefer
  changing the shared module so the page picks it up with a one-line import swap.
- **Lands before / alongside the three siblings.** Coverage-headline, quarantine-drilldown, and
  strict-indicative each say "reuse `QcBadge` tones, no new accent" — they are *relying on* there being
  one `QcBadge` and one palette. Ship this consolidation first or in lockstep so they conform to a real
  single system, not a forking one.
- **Pairs with examples-doc shortlist items 2 & 3** — the skeleton feedback state (this spec's feedback
  primitive) and the explanation-map/`InfoDot` (a separate spec) both want *one* shared primitive home.
  Don't create a third home; if `ui/` is adopted here, those build on it.
- **Reads only what's already on screen** — no look-ahead concern (no data path touched).

## What to do (ordered)

1. **Token derivation.** Make `chartTheme.ts` read the palette from the `:root` CSS vars rather than
   re-declaring hex literals (`getComputedStyle` at init, or a shared `lib/tokens.ts` consumed by both).
   Verify the chart colours render byte-identical to today (Plotly traces unchanged). Remove the
   now-redundant "keep in lockstep" comments.
2. **One `QcBadge`.** Extract to `components/QcBadge.tsx`; import in `marketHeader.tsx` and
   `FreshnessPanel.tsx`; delete the second copy. Output markup unchanged.
3. **One `clockTime`.** Add `clockTime(ts, fallback?)` to `lib/format.ts`; replace all three local
   copies; pick and document the single null-fallback policy.
4. **One feedback idiom.** `AsyncBlock`'s footprint-preserving skeleton loading state is
   [MAT-LEGIBILITY-skeletons]' `<ChartSkeleton>` (consume it); route the ops fetch loading/error through the
   *same* `AsyncBlock` (keep the job-state `.ops-pill` ledger). One "loading" look, one "error" look, both
   distinct from a populated panel.
5. **`Metric` can't go naked.** Add `unit`/`hint`; route quantitative callers through `sciUnit`. A
   non-quantitative caller (a time string) may opt out explicitly, but a number may not.
6. **One `Callout`.** Fold the three amber callouts into one toned component; delete the duplicate CSS.
7. **ADR for `ui/`.** Record: adopt the shadcn primitives as the home for shared components, or park them
   with a dated reason. Either way, no silent second vocabulary survives this task.
8. **No visual regression.** Confirm the full-coverage healthy-day surface is pixel-identical
   (Playwright screenshot / manual diff). The whole point is invisibility of the change to the PM's eye.

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values derived from a source other than the code
under test (assert user-visible text/tone, not internal state).

- **Token derivation — single source.** A test that flips a `:root` palette var (or its test-env
  equivalent) and asserts the chart theme value moves with it — proving the chart palette is *derived*,
  not copied. Snapshot the current `CHART_COLORS` hex against the `:root` hex to prove byte-identity after
  the refactor.
- **One `QcBadge` (component test).** `pass`/`fail`/`unknown` each render the expected text ("QC pass"
  /"QC fail"/"QC n/a") and `.qc-badge--{qc}` tone; assert both call sites (market header, freshness)
  render the *same* component output for the same verdict.
- **One `clockTime`.** A `recorded_ts` of `…T17:30:00…` → `"17:30:00"` at all three former call sites; a
  `null` ts → the one documented fallback, identically everywhere (oracle: a hand-written ISO string).
- **One feedback idiom.** A panel in loading state renders a skeleton/`role="status"` that reserves the
  panel footprint (no bare one-line "Loading…"); an error renders `role="alert"` in the error tone; a
  populated panel renders neither. The three read **differently** (assert roles + that loading ≠ error).
- **`Metric` can't render a naked number.** A quantitative value renders **with its unit** via `sciUnit`
  (e.g. `delta` → `… $/$`); the type/test prevents a bare number with no unit reaching the DOM.
- **One `Callout`.** `info`/`warn`/`ok` tones each render the expected palette class; the old `.gaps`
  /`.ops-backlog`/`.accepted-banner` call sites render through the one primitive.
- **No visual regression (Playwright).** A full-coverage healthy-day Onglet 1 + Operations page screenshot
  matches the pre-refactor baseline (the change is invisible to the eye).
- **Boldness budget (assertable).** On each consolidated screen, exactly one element carries the
  emphasis treatment in the default healthy state; assert no screen renders two simultaneous accents in
  the full/healthy case.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` **and**
  the web suite (`npm run lint && npm test`, plus `npm run e2e` for the screenshot/layout checks per
  `apps/frontend/README.md`).

## Done criteria

`chartTheme.ts` derives its palette/font from the `:root` tokens (no hex literals, no "keep in lockstep"
comment); there is exactly one `QcBadge`, one `clockTime`, one fetch loading/error idiom, and one
`Callout`, each imported at every former call site with the duplicates deleted; `Metric` cannot render a
naked quantitative number (everything goes through `sciUnit`/`UNITS`); the shadcn `ui/` layer is either
adopted or parked with a dated ADR — no silent second vocabulary remains; a full-coverage, healthy-day
surface is pixel-identical to today (proven by a Playwright screenshot diff, not by trusting the refactor);
both gates green. The owner's test holds: **the same verdict reads the same tone, the same number carries
its unit, and the same "loading" looks the same — everywhere — so the PM is never misled by an
inconsistent surface.**

## Gotchas

- **This must be invisible to the eye.** It removes divergence; it changes no look. A screenshot diff on
  a healthy day is the acceptance test. If you find yourself "improving" a colour or spacing, stop —
  that's a different spec and it needs owner sign-off (`frontend-is-owner-owned`).
- **Don't reinvent — that's the whole point.** The temptation is to "modernize" by ripping the
  battle-tested `index.css` over to Tailwind utilities wholesale. Do **not**: the unlayered legacy CSS is
  load-bearing and owner-validated (`index.css:10-17`), and a big-bang rewrite is exactly the
  death-by-a-thousand-design-systems anti-pattern. Consolidate the *dupes*; leave the working skin.
- **The `:root` ↔ `@theme inline` bridge is the asset — don't break it.** It is what already makes
  utilities and legacy CSS share one palette (`index.css:21-38`). The chart-theme derivation must read
  *through* it, not around it.
- **Keep the job-state ledger.** The `.ops-pill` queued/running/done/error pills (`RunControlPanel.tsx:16-21`)
  are a job *history*, a different thing from a fetch loading state — don't collapse them into the feedback
  primitive. Unify the *fetch* idiom only.
- **One null-fallback for time, documented.** The three time helpers disagree today ("time unknown" vs
  `null`); a PM seeing "time unknown" on one panel and a clean omission on another is a small lie about
  data presence. Pick one and apply it everywhere.
- **Don't fork the metric, don't fork the badge, don't fork the accent.** If the next feature wants a new
  amber box, it uses `Callout tone="warn"`; if it wants a verdict chip, it uses `QcBadge`. The boldness is
  the honesty of the one emphasized number per screen, said plainly — never a new colour.
