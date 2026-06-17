# MAT-LEGIBILITY-explanation-map — one "what-is-this / where-from" entry per metric, written once, read by the tooltip AND the assistant

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on" — and the
> 2026 way to deliver that is **AI-first**: hover an element and ask *"what is this / how do I read it /
> where did it come from"*. Today the answers already exist — they are scattered as inline constants
> (`SURFACE_LABEL` in `charts.tsx:40`, `SMILE_HEAD :231`, `GREEKS_SHAPE_HEAD :307`, the convexity gloss
> in `TenorPanel.tsx:27-29`, every scorecard `hint` in `Scorecards.tsx:69/77/87/92/101/112`, the sign
> legend `:131-136`). They are good prose, but they live *inside each component*, so the same metric can
> only be explained where that component happens to render it, and an assistant that wanted to say the
> same thing would have to re-author the copy — and could drift from, or worse invent, what the tooltip
> shows. This task gives every metric **exactly one** explanation entry, in PM language, that the ⓘ
> tooltip (Principle 5) and the in-app assistant (Principle 6) both consume. Write the words once; they
> can never diverge. (Design language [frontend-design-language-2026] Principle 2 + the examples doc
> §"Principle 6", item ✅ "first step": *lift those strings into one explanation map keyed by element id*.)

> **Cross-spec reconciliation (canonical — see [MAT-LEGIBILITY-build-order]).** This spec is the **sole
> owner** of the shared explanation map and the `<InfoDot>` primitive. The canonical names are
> **`lib/explain.ts`** (the map + `ExplainEntry` shape + `explainWithContext(id, ctx)`) and
> **`components/InfoDot.tsx`**. The sibling specs that earlier named their own copy — [MAT-LEGIBILITY-guidance]
> (`lib/help.ts` / `{title, body}` / `helpFor`), [MAT-LEGIBILITY-assistant] (`explanations.ts` /
> `{what, howToRead, provenance}`), and [MAT-LEGIBILITY-action-feedback] (the stage labels + button gloss) —
> are **consumers, not authors**: they read this map. The `{title, body}` / `{what, howToRead, provenance}`
> shapes are field-name aliases of the canonical `ExplainEntry` (`label` ≙ `title`, `whatIs`+`howToRead` ≙
> `body`, `whereFrom(ctx)` ≙ `provenance`); they collapse onto this shape, they do not fork it.

## What's true today (grounded in code)

- The "what is this" copy is **real and already written**, just trapped per-component:
  - `charts.tsx:40` `const SURFACE_LABEL = "Implied-volatility surface (vol vs log-moneyness vs maturity)"`
    (a constant; not even bound to the underlying — the §2b gap).
  - `charts.tsx:231` `const SMILE_HEAD = "implied vol vs log-moneyness; puts ◄ ATM ► calls"`.
  - `charts.tsx:307` `const GREEKS_SHAPE_HEAD = "raw Greeks vs strike; gamma/vega bell, delta S-curve…"`.
  - `TenorPanel.tsx:27-29` the convexity butterfly gloss (*"IV(25Δp) + IV(25Δc) − 2·ATM (vp = vol point
    = 0.01 IV)"*).
  - `Scorecards.tsx` — six `hint` strings (`:69, :77, :87, :92, :101, :112`) and the sign legend
    (`:131-136`).
- These are **what-is-this** copy. The **where-from** half ("source, as-of, computed-where, what was
  excluded" — Principle 2 #2) does **not** exist anywhere yet: the scorecard `(signal)` tag
  (`Scorecards.tsx:77`) is the only seed.
- **There is no assistant, no ⓘ tooltip, no `InfoDot`, no copy/explanation map** — confirmed: a repo-wide
  search for `assistant|explain|InfoDot|tooltip|copy_map|explanation` in `web/src` returns only unrelated
  prose. This is greenfield surfacing; nothing to refactor away.
- The data the assistant must cite is already on the page: `analytics.data` (`Market.tsx:49`,
  `AnalyticsResponse`), the persisted `Signal`s (`api.ts:405`, the scorecard inputs), and the as-of
  identity (`Market.tsx:46` `effectiveAsOf`, `:113` `qc`, the coverage block from
  [MAT-LEGIBILITY-coverage-headline]).

## Objective

A single **explanation map** — `lib/explain.ts` — keyed by a stable **metric id**, where each entry is
the one home for a metric's *what / how-to-read / where-from*. Two consumers read it and **only** it:

1. an `<InfoDot id="…"/>` tooltip primitive (the tier-2 "ⓘ" carrier, Principle 5), and
2. the future assistant (Principle 6), which answers "what is this?" by reading the **same** entry plus
   the live `analytics.data` already on screen.

Because the copy is written once and consumed in both places, the tooltip and the assistant can never
say different things about the same number — and the assistant has a *closed vocabulary* of metrics it
is allowed to explain, which is the data-layer guardrail against it inventing a metric or a number.

This task ships the **map + the static (`what` / `how-to-read`) copy + the `<InfoDot>` primitive + the
`where-from` *contract*** (the function that fills provenance from live state). It does **not** build the
assistant UI itself — but it is the seam the assistant plugs into, and it must be shaped so the assistant
needs no second copy source.

## Design intent (this is a contract, not a JSON dump)

The map is load-bearing precisely because two consumers depend on it being the *only* source. Three
properties make it trustworthy:

- **One entry per metric, keyed by a stable id** (`atm_level`, `term_structure_slope`, `iv_rank`,
  `skew_25d`, `rv_minus_iv`, `rho_bar`, `convexity_25d`, `nappe`, `smile`, `greek_profiles`,
  `surface_coverage`). The id is the join key the tooltip and the assistant share. An id used by a
  consumer that has no entry is a **build/test failure**, not a silent blank (see test surface).
- **Each entry is a typed shape, not a free string:**
  - `label` — the PM-register name (`"Pente de la structure par terme"` / EN per the page's vocabulary).
  - `whatIs` — one plain sentence: *what this number is*. (Lifted verbatim where good copy already
    exists — do not re-author `SMILE_HEAD`, *move* it.)
  - `howToRead` — one sentence: *how to read it / what good vs bad looks like* (e.g. the sign legend's
    *"> 0 = vol cheap (buy)"* lives here, once).
  - `unit` — the `lib/format.ts` `UNITS` token (or the per-metric vp/percent convention) so the tooltip
    can render the value in the house idiom; **never** a hard-coded unit string.
  - `whereFrom(ctx)` — a **function of live state**, not a constant: given the on-screen context
    (`underlying`, `asOf` + close instant, `mode` strict/indicative, `source`: `signal` vs
    front-projected, coverage) it returns the provenance sentence. This is the half that doesn't exist
    today and is the whole point of "where did this number come from?".
- **Plain words, PM register** (`analytics-pm-legible-framing`): "cotation", "deux-faces", "exclue",
  "clôture", "signal enregistré" — never "snapshot", "IV point", "quarantined row", "BFF", "run_id" *as a
  user-facing word* (a run id may appear as evidence, but glossed: *"capté le …"*). De-jargon the surface;
  the engine is untouched.

## ⛔ Hard guardrail (load-bearing — the assistant's honesty lives here, not in the model)

**The assistant must cite the same data the screen shows and never invent a number — and that is enforced
in this data layer, not the model's goodwill** ([frontend-design-language-2026] Principle 6, constraint 1;
examples doc §Principle 6). Concretely, this map encodes the guardrail:

- An explanation entry carries **no numeric values of its own** — only `whatIs`/`howToRead`/`unit` and a
  `whereFrom(ctx)` that formats *the context handed to it*. The number the assistant quotes always comes
  from the live `analytics.data` / `Signal` / coverage block, rendered through `lib/format.ts`, **never**
  baked into the copy.
- The assistant's "what am I looking at?" answer is **assembled** = `entry.whatIs` + `entry.howToRead` +
  `entry.whereFrom(liveCtx)` + the live value via `sciUnit`/`volPercent`. If `liveCtx` has no value for
  that metric, the answer says so (*"signal non enregistré pour cette clôture"*) — it does **not**
  fabricate one. This mirrors `Scorecards.tsx` already rendering `"—"` for a null read (`:75/:85/:99`).
- **Indicative guardrail rides along** ([MAT-LEGIBILITY-strict-indicative-mode]): when `ctx.mode ===
  "indicative"`, `whereFrom` must say the mark is indicative and *not the stored close*; it may never
  describe an indicative mark as the canonical close. The map is where that sentence is authored once.

## Owns

- **Front, new**: `lib/explain.ts` — the typed `ExplainEntry` shape, the `EXPLAIN` map (one entry per id
  above), and `explainWithContext(id, ctx)` that returns the assembled what/how/where-from for a given
  live context. Pure, no React, fully unit-testable.
- **Front, new**: `components/InfoDot.tsx` — a small, quiet `ⓘ` button that opens the entry's
  `whatIs`/`howToRead`/(optional `whereFrom`) in a non-modal popover/tooltip on hover **and** click
  (keyboard-reachable, `aria-describedby`). Tier-2 carrier; no new accent — reuse the existing type/colour
  tokens, the `QcBadge` family for any tone.
- **Front, refactor (surgical, owner-lane — coordinate)**: replace the *literal* copy in `Scorecards.tsx`
  (`hint`/legend), `TenorPanel.tsx` (convexity gloss), and the `*_HEAD`/`SURFACE_LABEL` constants in
  `charts.tsx` with reads from `EXPLAIN`, so the same words now live once. The chart **titles** keep
  binding to live state per §2b / [MAT-LEGIBILITY-coverage-headline]; this task only moves the *static
  explanatory clause* into the map, it does not regress the self-describing title.
- **Front, types**: extend `api.ts` only if `whereFrom` needs a provenance field not yet serialized
  (e.g. the metric `source` discriminator). If the BFF already carries it on the `Signal`
  (`api.ts:405`), read it; do not add a field that duplicates one.
- Tests both the map (unit) and `<InfoDot>` (component).

## Depends on / coordinates with

- **Frontend is owner-owned** (`frontend-is-owner-owned`): `apps/frontend/web` is Matthieu's exclusive
  lane. The fleet may **not** author the React/TSX here. The fleet's only allowed slice is an **additive,
  read-only BFF field** if `whereFrom` needs a provenance discriminator not yet on the wire — and even
  that is additive-nullable, never a contract break. Claim the `Market.tsx`/`charts.tsx`/`Scorecards.tsx`
  row on the board before any front edit; serialize behind the live `frontend-cockpit-ux` lane.
- **Shares the metric vocabulary** with [MAT-LEGIBILITY-coverage-headline] (the `surface_coverage` entry
  *is* the headline's copy — write it once, the headline and the tooltip read it) and
  [MAT-LEGIBILITY-strict-indicative-mode] (the `mode` term in `whereFrom`, and the provenance words
  `observed_two_sided | one_sided | last` must line up with that spec's per-point provenance and the
  quarantine reason taxonomy). **Do not fork the words** — a row excluded as `missing_side` in
  quarantine, included as a `one_sided` mark in indicative, and glossed in the tooltip must use the
  *same* French phrase in all three.
- **Unblocks the assistant** ([frontend-design-language-2026] Principle 6, the flagship): this is item ✅
  "first step (cheap, unblocks both P5 and P6)" in the examples doc shortlist (item 3). Ship this and the
  assistant has a grounded, closed vocabulary to read; skip it and the assistant re-authors copy and can
  drift.

## What to do (ordered)

1. **Enumerate the metric ids and lift the existing copy (no new prose where copy exists).** For each id,
   create the `EXPLAIN` entry by *moving* the literal string from its component: `atm_level`/`skew_25d`
   from `Scorecards.tsx` `hint`s and `computeScorecards`; `term_structure_slope`/`iv_rank`/`rv_minus_iv`/
   `rho_bar` from their `hint`s (`:77/:87/:101/:112`); `convexity_25d` from `TenorPanel.tsx:27-29`;
   `nappe` from `SURFACE_LABEL` (`charts.tsx:40`); `smile` from `SMILE_HEAD` (`:231`); `greek_profiles`
   from `GREEKS_SHAPE_HEAD` (`:307`); `surface_coverage` from the coverage-headline spec's copy. Split
   each into `whatIs` (the noun phrase) + `howToRead` (the sign/threshold clause from the legend
   `:131-136`).
2. **Write the `whereFrom(ctx)` functions.** Author the provenance sentence per metric from live context:
   a scorecard signal → *"signal enregistré · {tenor} · clôture {asOf} 17:30 CET"* (SX5E close is 17:30
   CET, **not** 22:00 — `sx5e-close-instant-1730-cet`); a front-projected read (ATM/skew off the smile) →
   *"projeté depuis le smile {tenor} · clôture {asOf}"*; the nappe → the coverage clause + mode. Where the
   value is absent, the sentence says *"non enregistré"*, never a guess.
3. **Build `<InfoDot>`.** Quiet ⓘ, hover **and** click (touch/keyboard), non-modal, `aria-describedby`
   wiring the trigger to the popover; renders `label` + `whatIs` + `howToRead`, and `whereFrom(ctx)` when
   a context is passed. Reuses existing tokens; no modal, no new accent.
4. **Refactor the components to read the map.** Replace the inline constants/`hint`s with `EXPLAIN[id]`
   reads and hang an `<InfoDot id=…/>` on each metric/chart heading. Keep the §2b self-describing chart
   *title* bound to live state — only the static explanatory clause moves to the map.
5. **Shape the assistant seam (no UI).** Export `explainWithContext(id, ctx)` and a registry of valid ids,
   so the assistant's later "explain this" reads the map + live `analytics.data` and is structurally
   unable to reference a metric outside the registry. Document the seam in `apps/frontend/README.md`.
6. **No look-ahead, no recompute.** The map is copy + a pure provenance formatter over context the page
   already holds; it reads no store, recomputes no analytics, and the `whereFrom` `asOf` is the
   *requested/resolved* date, never a later one.

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **Single source of truth — no orphan id, no orphan entry (the load-bearing test).** Assert every id the
  components/assistant pass to `<InfoDot>`/`explainWithContext` has an `EXPLAIN` entry, and (optionally)
  every entry is referenced — so a renamed metric can't leave a tooltip blank or an entry stale. A missing
  id is a test failure, not a silent gap.
- **Copy moved, not duplicated — regression.** Assert the metric's `whatIs`/`howToRead` text now comes
  from `EXPLAIN` and the old inline literal is gone (grep-style assertion or a render snapshot equal to the
  map entry) — proving the words live in one place, the whole point of the task.
- **`whereFrom` is a pure function of context, with a hand-built oracle.** For a fixture ctx
  (`underlying="SX5E", asOf="2026-06-17", mode="strict", source="signal", tenor="3m"`) assert the exact
  expected sentence including **"clôture 2026-06-17 17:30 CET"** (not 22:00, not bare date). A `mode:
  "indicative"` ctx → the sentence names it indicative and *not the stored close*. A `value: null` ctx →
  *"non enregistré"*, no fabricated number.
- **Assistant grounding — no invented number (data-layer guard).** `explainWithContext` returns only
  copy + a formatter over the passed value; assert that with a `null`/absent value it never emits a
  numeral, and that an id outside the registry throws/returns a typed "unknown metric" rather than free
  text. This is the test that enforces Principle 6's honesty in the data layer.
- **`<InfoDot>` component test.** Renders the ⓘ; hover **and** click open the popover with the entry's
  `whatIs`+`howToRead`; it is `role`-correct and keyboard-reachable (`aria-describedby` present), non-modal
  (does not trap focus / does not block the page). Assert user-visible text + roles, not internal state.
- **No look-ahead.** A `whereFrom` ctx for past date D ignores any later date; `check-lookahead-bias`
  clean on the read path (the map touches no store, but assert the `asOf` formatting uses the resolved
  date only).
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` (only if
  an additive BFF provenance field is touched) **and** the web suite (`tsc + lint + vitest + playwright`).

## Done criteria

`lib/explain.ts` holds **one** entry per metric id (PM-register `label` + `whatIs` + `howToRead` + `unit`
+ a live-context `whereFrom`), built by *moving* the copy that today lives inline in `Scorecards.tsx`,
`TenorPanel.tsx`, and `charts.tsx` — those components now read the map and hang an `<InfoDot id=…/>` on
each metric, so the same words appear once; the `surface_coverage` entry is the coverage headline's copy
(not a fork); `whereFrom` renders provenance from live state (close instant 17:30 CET, strict/indicative
mode, signal vs projected) and never bakes in a number; `explainWithContext(id, ctx)` is the grounded
seam the assistant will read, structurally unable to reference an unknown metric or invent a value; a test
fails if any consumed id lacks an entry; the §2b self-describing chart titles are unregressed; no
look-ahead; both gates green.

## Gotchas

- **Surface only — move copy, don't rewrite it.** The good prose exists (`SMILE_HEAD`, the scorecard
  hints, the convexity gloss). Lifting it verbatim into the map is the win; re-authoring it risks
  regressing copy the owner already tuned. If a sentence isn't already on screen, the only *new* prose is
  the `whereFrom` provenance half.
- **Don't fork the metric or its words.** `surface_coverage` shares copy with
  [MAT-LEGIBILITY-coverage-headline]; the provenance words share the taxonomy with
  [MAT-LEGIBILITY-strict-indicative-mode] and the quarantine reasons. One vocabulary, three consumers.
- **The honesty is in the data layer, not the model.** The reason this is a *map* and not "let the
  assistant describe the screen" is that a map is a closed vocabulary the assistant cannot exceed, and a
  copy-only entry is a number it cannot fabricate. Don't water this down into a prompt that "tries to be
  accurate."
- **Frontend is owner-owned.** The fleet does not author the TSX here; the only fleet-eligible slice is an
  additive read-only BFF provenance field, additive-nullable. Attribute and coordinate before touching
  `Market.tsx`/`charts.tsx`/`Scorecards.tsx`.
- **Don't reinvent the design system.** `<InfoDot>` rides the existing tokens and `QcBadge` tones; numbers
  ride `lib/format.ts`. No new accent for "help" — the boldness is spent on the coverage headline / the
  INDICATIF badge, not on a help dot.
- **Close instant is 17:30 CET.** Any `whereFrom` that prints an as-of for SX5E uses the OESX settlement
  instant 17:30 CET, never the 22:00 futures close (`sx5e-close-instant-1730-cet`).
