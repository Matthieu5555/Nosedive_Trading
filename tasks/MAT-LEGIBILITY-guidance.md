# MAT-LEGIBILITY-guidance — point, flash, get out of the way (no modal tour)

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on." The cockpit
> should *teach itself*: hover an element to learn what it is, and have the right thing **flash** when
> it's the PM's turn to act — but never a front-loaded product tour nobody reads. Today Onglet 1
> (Market) opens with an *empty* index selector (`Market.tsx:26` `const [index, setIndex] = useState("")`)
> and a first-time PM gets **zero** interactive help: no ⓘ hotspot, no hover gloss, no next-step hint
> (examples doc, Principle 5: *"There is **zero interactive contextual help**"*). The good inline
> glosses we do have — the convexity formula (`TenorPanel.tsx:27-29`), the Scorecards sign legend
> (`Scorecards.tsx:131-135`) — are hardcoded prose buried in each component, not a reusable affordance.
> This task is **Principle 5** of the design language, the just-in-time half: contextual guidance that
> points and flashes, and gets out of the way.

> **Cross-spec reconciliation (canonical — see [MAT-LEGIBILITY-build-order]).** The explanation map and the
> `<InfoDot>` primitive are **owned by [MAT-LEGIBILITY-explanation-map]** (`lib/explain.ts`,
> `ExplainEntry`, `explainWithContext`, `components/InfoDot.tsx`) — this spec **consumes** them, it does not
> create a second copy. Where this spec says `lib/help.ts` / `HELP` / `helpFor(id)` / `{title, body}`, read
> the canonical `lib/explain.ts` / `EXPLAIN` / the map accessor / `ExplainEntry` instead (`title` ≙ `label`,
> `body` ≙ `whatIs`+`howToRead`). What this spec **uniquely owns** is the **`PulseHint` next-step flash**,
> the pulse/motion CSS, and the `prefers-reduced-motion` rule. Build order: explanation-map lands `InfoDot`
> + the map first; this spec then adds `PulseHint` and the mount points. The "one copy source" rule the two
> specs both invoke is satisfied by there being **one** map (`lib/explain.ts`), not two.

## What's true today (grounded in code)

- **Nothing interactive exists.** `grep -rni "infodot|tooltip|hotspot|pulse|coachmark|spotlight"` over
  `apps/frontend/web/src` returns **nothing** (verified 2026-06-17). There is no help primitive to reuse;
  this task creates the first one.
- **The glosses are already written, just trapped.** The "what is this / how to read it" copy lives
  inline as constants and JSX: `SURFACE_LABEL` (`charts.tsx:40`), the convexity butterfly hint
  (`TenorPanel.tsx:27-29`), the Scorecards sign legend (`Scorecards.tsx:131-135`), each scorecard `hint`
  (`Scorecards.tsx:126`). These are the right voice (PM register, plain words) but unreachable on demand.
- **The first-load empty selector is the genuine next-step moment.** `Market.tsx:26` initialises
  `index` to `""`; a `useEffect` (`:27-32`) auto-selects the first option *once `/api/indices` resolves*,
  but during that window — and any time `indexOptions.length === 0` — the page is a header with an empty
  `<select aria-label="Index">` (`:76-91`) and an `AsyncBlock` (`:100-103`) below it. That empty selector
  is the one place a "click here to begin" pulse is honestly warranted.
- **The CSS system has no motion vocabulary yet.** `index.css` (1 592 lines) contains **no** `@keyframes`
  and **no** `prefers-reduced-motion` rule (verified). It *does* import `tw-animate-css` (`index.css:19`),
  a maintained animation-utility library already on the project — use it; do not hand-roll a keyframe.
- **The design tokens to ride** are `--amber` (`index.css:53`), `--muted`/`--faint` (`:49-50`),
  `--blue` (`:54`) — the existing palette. No new accent (Principle 7).
- **`tw-animate-css` is in.** `RunControlPanel` is the action-feedback exemplar but unrelated; the only
  shadcn primitive near this is `ui/dialog.tsx` — **do not** use it (a dialog is a modal; this task is
  explicitly *not* a modal).

## Objective

Two affordances, one shared copy source, no modal anywhere:

1. **ⓘ hotspots — the default carrier for "what is this?"** A single small `<InfoDot>` primitive that any
   element can hang a one-line explanation on. Quiet by default (a small `ⓘ` glyph in `--faint`), opens a
   non-modal tooltip on hover **and** focus/click (keyboard-reachable), closes on blur/Escape. It is
   *inline* — it never dims the page, never blocks the workflow.

2. **A pulsing next-step hint — the literal "flash when it's your turn."** Exactly one, reserved for the
   genuine next-step moment: the index selector on first load when nothing is chosen
   (`Market.tsx:26` `index === ""`). It pulses to say "start here", and **stops the instant the PM acts**
   (an index is chosen) — it never re-fires for the rest of the session.

The copy both consume comes from **one explanation map** keyed by element id (the seed the examples doc
calls for, item 3 of the shortlist) — written once, so the ⓘ tooltip can never drift from what a future
assistant (Principle 6, separate task) will read. That map and the `<InfoDot>` primitive are owned by
[MAT-LEGIBILITY-explanation-map] (`lib/explain.ts` / `components/InfoDot.tsx` — see the reconciliation note
above); this task **consumes** them and uniquely owns the **`PulseHint` next-step flash** and its motion;
the assistant also only *consumes* the map.

## Design intent (this is a designed element, not a chrome)

- **Hotspot, not billboard** (Principle 1, progressive disclosure). The ⓘ is tier-2: present but
  receding, "there's more here". The headline number stays the loud thing; the ⓘ is the quiet "more".
  An ⓘ that draws the eye away from the number it annotates is a bug.
- **Non-modal, always** (Principle 5, the load-bearing rule). Tooltip is positioned inline, dismissible,
  and the page stays fully interactive behind it. **No spotlight/mask in v1** — that is "do this next"
  heavy machinery the assistant will drive later; here we only do the lightweight hover-gloss + the one
  pulse. The anti-pattern this task exists to *prevent* is a `ui/dialog.tsx` welcome tour.
- **The pulse is rationed.** Principle 5: *"Reserve it for genuine next-step moments… over-used, it
  becomes noise."* Exactly one pulse target (the empty index selector), exactly one condition
  (`index === ""` with options available or still loading), and it dies on first interaction. A pulse on
  a selector that already has a value is the noise the principle forbids.
- **Plain words, PM register** (`analytics-pm-legible-framing`). The explanation copy is the system's
  voice in French/EN as the surface already uses — "Nappe de volatilité", "deux-faces", "ténor" — never
  raw enums or engine terms (`surface_slice`, `snapshot`, `iv_points`). A gloss that needs a glossary
  is not a gloss.
- **One copy source, consumed twice-then-thrice.** The explanation map is the single home for
  "what is this / how to read it". The ⓘ reads it now; the assistant reads it later. Writing the same
  string in two components is the drift this map exists to kill — the same anti-drift discipline as the
  reason→label map in [MAT-LEGIBILITY-quarantine-drilldown].
- **Motion respects the system and the user.** Use `tw-animate-css` (`index.css:19`) for the pulse; do
  not hand-roll a keyframe. Add the **one** missing `@media (prefers-reduced-motion: reduce)` rule that
  disables the pulse animation (replace it with a static, still-visible emphasis — e.g. a steady ring),
  because today the app has no reduced-motion handling at all and a pulse is the first thing that needs it.
- **No silent state, applied to help** (Principle 3). An ⓘ whose map entry is missing must render
  *nothing* (no empty bubble, no `undefined`) — the absence of help is silent and fine; a broken,
  empty tooltip is a visible lie. Test this.

## Owns

- **Front only.** No BFF change — this surfaces copy and motion, reads no new data. (If a later assistant
  task needs the map server-side, that is its task, not this one.)
- **The explanation map + `<InfoDot>` are NOT owned here** — they are [MAT-LEGIBILITY-explanation-map]'s
  (`lib/explain.ts` + `components/InfoDot.tsx`). This spec **consumes** them. The id set this spec needs
  (`"nappe"`, `"smile"`, `"convexity_25d"`, `"rv_minus_iv"`, `"index-selector"`, `"as-of-selector"`, …)
  must be contributed to / present in the canonical `EXPLAIN` map and to the canonical `ExplainEntry`
  shape (`label`/`whatIs`/`howToRead`); the strings are lifted **once**, there. If explanation-map has not
  landed yet, this spec may stub the same `lib/explain.ts` against its typed contract — never a parallel
  `lib/help.ts`. The mount-time selector ids (`index-selector`, `as-of-selector`) are this spec's
  additions to that one map.
- **`components/PulseHint.tsx` (new) — the next-step flash.** A wrapper `{ active: boolean; children }`
  that applies the pulse animation to its child when `active`, nothing when not. Drive it in `Market.tsx`
  around the index `<select>` (`:76-91`) with `active={index === "" }`.
- **CSS**: `.info-dot` + `.info-tooltip` rules in `index.css` (ride `--faint`/`--muted`/`--panel-soft`,
  reuse the panel/border grammar); a `.pulse-hint` class using `tw-animate-css`; the **one**
  `@media (prefers-reduced-motion: reduce)` rule. No new color token.
- **Mount points in `Market.tsx`**: an ⓘ next to the **Volatility nappe** `<h2>` (`:171`), the **Price**
  `<h2>` (`:147`), and the **Dispersion (ρ̄)** `<h2>` (`:199`); the `PulseHint` wrapper on the index
  selector (`:76-91`). The convexity ⓘ goes on `ConvexityReadout` (`TenorPanel.tsx:24`), reading the same
  `convexity-25d` entry the hint text was lifted into.
- **Tests** (front): component tests for `InfoDot` (opens on hover/focus, closes on Escape, renders
  nothing on unknown id), `PulseHint` (pulses only when active), the map (lifted strings present); plus
  an e2e assertion on the first-load pulse + a no-modal guard.

## Depends on / coordinates with

- **Sibling of the three [MAT-LEGIBILITY] specs and the umbrella doc**
  (`tasks/frontend-design-language-2026.md`, Principle 5; examples doc Principle 5 §). Those prove
  Principles 1–3 + 7; this is the first delivery of **Principle 5**. It is **standalone** — it depends on
  none of them and unblocks the assistant.
- **Unblocks Principle 6 (the assistant), shares the map.** The examples-doc shortlist item 3 is
  *"The explanation map + `<InfoDot>` — centralize the scattered head/hint copy (unblocks P5 and P6 at
  once)"*. This task **is** that item. Write the map so the assistant can `import { HELP } from "lib/help"`
  unchanged. Do not fork a second copy of any gloss.
- **Frontend is owner-owned** (`frontend-is-owner-owned`): `apps/frontend/web` is Matthieu's exclusive
  lane. This spec is the design; a frontend change lands by the owner's process. The fleet must not edit
  the React/web files for this — the spec defines *what*, the owner's lane builds it.
- **Shared-tree:** the mounts touch `Market.tsx:147/171/199` and the index `<select>` (`:76-91`) — the
  same `market-scroll` region the coverage-headline spec mounts under. Coordinate the single placement
  edit; the new `PulseHint` component + the pulse/motion CSS rules are disjoint (the map `lib/explain.ts`
  and `InfoDot` are explanation-map's, consumed here).
- **Reuse, don't reinvent** (Principle 7): ride `tw-animate-css` (`index.css:19`) and the existing
  tokens; do **not** add a motion library, a new accent, or a tooltip dependency. `ui/dialog.tsx` exists
  but is **out of bounds** — a dialog is the modal this task rejects.

## What to do (ordered)

1. **Consume the explanation map (don't fork it).** The map is the canonical `lib/explain.ts`
   ([MAT-LEGIBILITY-explanation-map]); confirm the ids this spec mounts exist there (`convexity_25d`,
   `nappe`, the sign-legend entry) — the strings are lifted once, in that spec. Add only the
   guidance-specific selector ids (`index-selector`, `as-of-selector`) to the same map. Do **not** create
   `lib/help.ts`. (If a string is still inline because explanation-map hasn't landed, coordinate the lift
   there rather than duplicating it here; the test asserts one source.)
2. **`InfoDot` is consumed, not built here** — it ships in [MAT-LEGIBILITY-explanation-map]. This spec
   mounts it. If sequencing forces this spec first, build `components/InfoDot.tsx` against the canonical
   `ExplainEntry` contract so explanation-map adopts it unchanged — one component, one home.
3. **`PulseHint`.** Wrapper applying the `tw-animate-css` pulse only when `active`. Add the `.pulse-hint`
   CSS + the `prefers-reduced-motion` fallback (static ring, still visible).
4. **Mount.** ⓘ on the nappe/price/dispersion headings and on `ConvexityReadout`; `PulseHint` around the
   index `<select>` with `active={index === ""}`. Confirm the pulse stops the instant `index` becomes
   non-empty (the existing `:27-32` auto-select will trip it; a manual choice trips it too).
5. **Reduced-motion + a11y.** Add the single `@media (prefers-reduced-motion: reduce)` rule. Verify the
   ⓘ is reachable by keyboard (Tab to it, Enter/Space opens, Escape closes) — the e2e covers it.

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **Map is the single source — anti-drift guard.** Assert the lifted strings are present in `HELP`
  (e.g. the convexity entry contains `"IV(25Δp) + IV(25Δc) − 2·ATM"`) **and** that `ConvexityReadout`
  now renders that exact entry (not a second literal). A grep-style test that the old inline literal no
  longer appears in `TenorPanel.tsx`/`Scorecards.tsx`/`charts.tsx` locks the no-fork rule.
- **`InfoDot` behaviour (component test).** Hover/focus opens a `role="tooltip"` with the entry's title +
  body; Escape and blur close it; the button carries `aria-label`/`aria-describedby`. **Unknown id renders
  `null`** — assert nothing visible, no empty bubble (Principle 3 applied to help).
- **`PulseHint` (component test).** `active=false` → child renders with **no** pulse class; `active=true`
  → pulse class present. The wrapper never blocks pointer events on its child (the selector is still
  clickable while pulsing).
- **First-load pulse, then quiet (e2e, `market-read-flow` style).** With the index unset on load, the
  selector carries the pulse; after choosing/auto-selecting an index, the pulse class is gone and does not
  return on later interactions in the session.
- **No-modal guard (e2e).** After mounting the guidance, opening any ⓘ does **not** insert a
  `role="dialog"`/modal overlay and does **not** dim or disable the page behind it — the page stays
  interactive (assert a behind-the-tooltip control is still clickable). This is the principle's hard line.
- **Reduced-motion.** Under `prefers-reduced-motion: reduce` the pulse animation is suppressed but the
  next-step emphasis is still visibly present (a static ring), not gone.
- Gate green: the web suite — `npm run lint && npm test` (tsc + ESLint + Vitest) **and** the opt-in
  Playwright e2e (`npm run e2e`), since this touches a page, shared layout, and first-load flow
  (`apps/frontend/README.md`). The backend gate is untouched (no BFF change).

## Done criteria

A reusable `<InfoDot>` ⓘ hotspot carries a one-line PM-register gloss on hover **and** keyboard focus,
non-modal and dismissible, on the nappe / price / dispersion headings and the convexity readout; one
pulsing next-step hint flashes the index selector on first load and stops the instant an index is chosen,
never re-firing; both affordances read their copy from the single canonical `lib/explain.ts` map
([MAT-LEGIBILITY-explanation-map]) with **no** duplicated gloss left in the components; the pulse honours
`prefers-reduced-motion`; opening any ⓘ never inserts a
modal or dims the page; an unknown help id renders nothing rather than an empty bubble; the web gate
(tsc + lint + vitest) and the e2e suite are green. The owner's test holds: a first-time PM is told where
to start and can ask "what is this?" of any annotated element — and is never trapped behind a tour.

## Gotchas

- **No modal, ever.** The whole point of Principle 5's just-in-time framing is to *avoid* the
  front-loaded modal tour. `ui/dialog.tsx` is out of bounds here; if the build reaches for a dialog, it
  has missed the spec.
- **Ration the pulse.** Exactly one target (empty index selector), one condition (`index === ""`), dies
  on first action. A pulse anywhere else, or one that re-fires, is the noise the principle forbids — and
  "a flash that always flashes" trains the eye to ignore the one time it matters (same failure mode as
  "a banner that always shouts").
- **Move the copy, don't copy it.** The map's value is being the *single* source. If after this task the
  convexity sentence exists both in `TenorPanel.tsx` and the canonical `lib/explain.ts`, the task created the very drift it
  was meant to remove. The test greps for the old literal's absence on purpose.
- **The app has no motion vocabulary yet.** There is no `@keyframes`, no `prefers-reduced-motion` in
  `index.css` today. Use `tw-animate-css` (already imported, `index.css:19`); add the one reduced-motion
  rule. Do not introduce a second animation dependency.
- **Don't reinvent the design system.** Ride `--amber`/`--faint`/`--muted`/`--panel-soft` and the panel
  grammar; no new accent (Principle 7). The boldness is spent on the one pulse, not on a new palette.
- **Help that can't load is silent, not loud.** Unlike a data error (loud, red), a missing help entry
  renders nothing — help is tier-2; its absence must not break the surface or leave an empty tooltip.
