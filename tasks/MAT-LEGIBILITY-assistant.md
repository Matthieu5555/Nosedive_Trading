# MAT-LEGIBILITY-assistant — a grounded screen-aware assistant that cites the same data, never invents a number

> **Owner ask (2026-06-17).** "It should be AI-first: an assistant you can talk to that explains what
> you're looking at, that you can hover an element and ask 'what is this / how do I do that'."
> ([frontend-design-language-2026] Principle 6.) But the load-bearing constraint comes straight from the
> anti-pattern list: *"an assistant that hallucinates a number instead of citing the one on screen — worse
> than no assistant."* This is the flagship surface of the legibility theme and the highest-risk one: a
> chat box that confidently states an IV the screen never showed would undo every honest-number guarantee
> the three sibling specs ([MAT-LEGIBILITY-coverage-headline], [MAT-LEGIBILITY-quarantine-drilldown],
> [MAT-LEGIBILITY-strict-indicative-mode]) buy us. So the whole task is engineered around one rule:
> **the assistant can only say a number that is already in the data the page is holding** — enforced in the
> data layer, not in the model's goodwill.

## ⛔ Hard guardrail (load-bearing — do not weaken)

**The assistant never originates an analytics number.** Every quantitative claim it makes (an IV, a
coverage fraction, a Greek, an as-of instant, a strike) must be a value lifted verbatim from the
**grounding context** the BFF assembles from the *same* store reads `/api/analytics` already serves — not
free-text the model produced. Encode this two ways:

1. **In the data layer.** The model is given a typed, pre-formatted **facts block** (numbers already run
   through the house `sci`/`sciUnit` idiom) plus the as-of/mode/coverage frame. Its system prompt forbids
   computing, interpolating, or estimating any analytics value; if a number isn't in the facts block, the
   correct answer is *"je ne l'ai pas à l'écran"*, never a guess. This is [frontend-design-language-2026]
   Principle 3 (no silent state) applied to the assistant: an ungrounded answer is a *loud* "I don't have
   that", not a confident fabrication.
2. **It respects the strict/indicative guardrail.** It will *explain* indicative mode but must **never**
   present an indicative mark as the stored close (the load-bearing rule in
   [MAT-LEGIBILITY-strict-indicative-mode]). When indicative is active the facts block is tagged
   `INDICATIF`, and the assistant carries that tag into every sentence about those marks.

If the assistant can state a number the screen never showed, the task has failed even if the chat reads
beautifully.

> **Cross-spec reconciliation (canonical — see [MAT-LEGIBILITY-build-order]).** The shared explanation map
> is **owned by [MAT-LEGIBILITY-explanation-map]** and its canonical name is **`lib/explain.ts`** (shape
> `ExplainEntry { label, whatIs, howToRead, unit, whereFrom(ctx) }`, accessor `explainWithContext(id, ctx)`).
> Where this spec says `explanations.ts` or the shape `{ what, howToRead, provenance }`, read the canonical
> `lib/explain.ts` / `ExplainEntry` (`what` ≙ `whatIs`, `provenance` ≙ `whereFrom(ctx)`). This spec **does
> not create** the map — it **consumes** it; if explanation-map has not landed, ship the assistant against
> its typed contract and let explanation-map lift the inline strings. The assistant uniquely owns the BFF
> grounding builder, the OpenRouter client, `/api/assistant`, and `AssistantPanel.tsx`. The `whereFrom(ctx)`
> provenance function in `lib/explain.ts` is the front-side gloss; the assistant's numeric grounding is the
> **server-built facts block** — they are complementary, not two copies of the same thing.

## What's true today (grounded in code)

- **Nothing exists yet — confirmed greenfield.** A read-only sweep (`grep -rinE "openrouter|llm|assistant|
  chat|anthropic" apps/frontend/src apps/frontend/web/src`) finds **no** assistant wiring on either side;
  the only `chat` hit is a comment in `web/src/lib/queryClient.ts:9`. So this is net-new, not a retrofit.
- **The grounding material is already written, just scattered as inline constants.** The "what is this /
  how to read it" copy lives in: `charts.tsx:40` (`SURFACE_LABEL`), `charts.tsx:231` (`SMILE_HEAD`),
  `charts.tsx:307` (`GREEKS_SHAPE_HEAD`), `TenorPanel.tsx:28-29` (the 25Δ butterfly gloss), every
  `Scorecards.tsx` `hint` (`:69`, `:77`, `:92`, `:100`, `:111`) and its sign legend (`:131-136`), and
  `api.ts:433-442` (`SIGNAL_CAPTIONS` — already one plain-language sentence per signal kind). These are the
  raw material for the explanation map (see [frontend-design-language-2026-examples] Principle 6, the
  "lift those strings into one explanation map" step) — write the copy once, consume it in both the tooltip
  and the assistant so they can never diverge.
- **The page already holds the exact data the assistant must cite.** `Market.tsx:49` fetches the
  `AnalyticsResponse` (`api.ts:248-254`) into `analytics.data`; the active frame is the
  `(index, effectiveAsOf, mode)` tuple `Market.tsx` holds at `:26`, `:46` (mode arrives with
  [MAT-LEGIBILITY-strict-indicative-mode]). The status line `Market.tsx:116-120` already states
  *subject · as-of · QC* — the assistant answers "what am I looking at?" from the **same** state.
- **Numbers have one rendering law.** `lib/format.ts` `sci`/`sciUnit` (`:22-48`) + the `UNITS` vocabulary
  (`:57-94`) is how every analytics number reaches the screen. The facts block reuses it so the assistant's
  numbers are byte-identical to the scorecards'.
- **The BFF is a thin FastAPI seam over the offline store.** Routers register in `app.py:55-95`; each reads
  `ctx.store` via `CtxDep`/`TradeDateDep` (`deps.py:19-38`); `AppContext` (`context.py:42-74`) already reads
  `os.environ` for config. A new `/api/assistant` router slots in exactly like `analytics.py:297-358`.

## Objective

A **non-blocking assistant panel** on Onglet 1 that can:

1. **Explain the current screen** — "Qu'est-ce que je regarde ?" → it reads the active surface, mode,
   as-of, and coverage and answers in PM French/EN, citing the same provenance the status line shows.
2. **Answer "c'est quoi, ça ?" for a hovered/selected element** — the smile, the nappe, a scorecard, the
   coverage headline → the gloss plus where the number came from, from the **shared explanation map** the
   ⓘ tooltip also reads.
3. **Answer "comment je fais pour… ?"** — e.g. "comment voir pourquoi des lignes sont exclues ?" → it tells
   you *and* (phase 2, optional) can make the affordance flash / open it (tie to the future ⓘ/spotlight).

The model runs **through OpenRouter** (the owner's chosen front door), called from the **BFF**, never the
browser — so the key never ships to the client and grounding is enforced server-side. The default model
behind OpenRouter is **`anthropic/claude-opus-4-8`** (OpenRouter's slug for `claude-opus-4-8`); the model
choice is config, the grounding contract is not.

## Design intent (this is a designed surface, not a chat widget bolted on)

- **Non-blocking, summonable.** A panel you open (a button in the page header / a docked side panel), never
  a modal you must dismiss to use the page ([frontend-design-language-2026] Principle 6: "a panel you
  summon, not a wall you must pass"). Closed by default; the page is fully usable without it.
- **Every answer wears its provenance.** When the assistant states a number it shows the same frame the
  page does — *SX5E · clôture 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations* — as a quiet caption
  on the answer, so the PM sees the assistant and the chart agree. (SX5E close instant is **17:30 CET**,
  OESX settlement — never 22:00; the facts block carries the resolved instant, the assistant never
  re-derives it.) This is Principle 2 (provenance) delivered conversationally.
- **Grounded-or-silent, loudly.** If a question needs a number not in the facts block, the answer is the
  honest gap in PM register: *"Ça n'est pas dans ce que l'écran affiche pour cette clôture — je ne vais pas
  l'inventer."* — never a plausible-looking fabrication. Same honesty bar as the degenerate-surface copy.
- **One explanation map, two consumers.** The "what is this / how to read it / where it comes from" copy is
  centralised in the single canonical `lib/explain.ts` map ([MAT-LEGIBILITY-explanation-map]) keyed by
  element id; the ⓘ tooltip (P5 work) and the assistant both read it. Writing it twice is how the tooltip and
  the assistant drift apart — forbidden.
- **Plain words, PM register** ([analytics-pm-legible-framing]): "cotations", "deux-faces", "exclues",
  "clôture" — never "quarantined rows", "IV points", "snapshots", "run_id". The assistant de-jargons; the
  engine is untouched.
- **No silent state in the panel itself** ([frontend-design-language-2026] Principle 3 / `frontend-no-silent-failures`):
  a request in flight shows a thinking indicator (not a frozen box); an OpenRouter/BFF failure renders a
  **loud** inline error (`role="alert"`) saying the assistant is unavailable — it never silently returns
  nothing or, worse, a stale answer. Reuse the existing failure idiom (`AsyncBlock.tsx:17-23` / the
  `GlobalErrorBanner` tone), no new accent.
- **Reuse the design system** ([frontend-design-language-2026] Principle 7): `QcBadge` tones
  (`marketHeader.tsx:3-10`), `lib/format.ts` for any rendered number, the locked reading model. No new
  palette, no second vocabulary.

## Owns

- **BFF — grounding + the OpenRouter call (the heart of the task).**
  - A `grounding.py` builder that, for `(underlying, as_of, mode)`, assembles a typed **`GroundingContext`**
    from the *same* store reads `analytics.py` uses (`projected_option_analytics`, `surface_parameters`,
    `qc_results`) — the active frame (subject, resolved close instant **17:30 CET for SX5E**, mode,
    coverage fraction) plus a bounded **facts block**: the on-screen scorecard numbers, the per-tenor smile
    points actually plotted, the coverage `option_rows/two_sided/excluded` (shared with
    [MAT-LEGIBILITY-coverage-headline]'s `coverage` block — **do not recompute it**), each already rendered
    through a server-side mirror of `sci`/`sciUnit`/`UNITS`. **It computes no new analytics value** — it
    reads and formats what the page already shows.
  - A `routers/assistant.py` `POST /api/assistant` (registered in `app.py:55-95` like every sibling): body
    `{ question, underlying, trade_date, run_id?, mode?, element_id? }`; it builds the `GroundingContext`,
    composes the system+user prompt (facts block + the explanation-map entry for `element_id` if present),
    calls OpenRouter, and returns `{ answer, citations[], grounded: bool, frame }`. `grounded=false` +
    the honest-gap answer when the question can't be served from the facts block; never a 500 on a model
    error — a labelled `{error, detail}` the front renders loud (mirror `app.py:45-53`'s labelled-400 path).
  - **OpenRouter client**: a thin wrapper reading `OPENROUTER_API_KEY` from the environment (via
    `AppContext`, like `context.py:64`'s `os.environ` reads) and `ASSISTANT_MODEL` (default
    `anthropic/claude-opus-4-8`). Key lives in the gitignored `.env` / `$HOME`, **never** in git, never in
    the browser bundle ([AGENTS.md] "no secrets in git"). The grounding rules ride in the **system prompt**
    *and* are enforced structurally: the model is given only the facts block, and the response is validated
    against it (see Test surface) — the model's compliance is a backstop, not the guarantee.
- **Front — the panel + the shared explanation map (owner's lane).**
  - **The explanation map is NOT created here** — it is the canonical `lib/explain.ts`
    ([MAT-LEGIBILITY-explanation-map]), keyed by element id (`nappe`, `smile`, `surface_coverage`,
    `atm_level`, `skew_25d`, `rv_minus_iv`, `rho_bar`, `convexity_25d`, …), each an `ExplainEntry`
    (`label`/`whatIs`/`howToRead`/`unit`/`whereFrom(ctx)`), **populated by lifting** the existing inline
    strings (`charts.tsx:40/231/307`, `TenorPanel.tsx:28-29`, the `Scorecards.tsx` hints, `api.ts:433-442`
    `SIGNAL_CAPTIONS`). This spec **consumes** that one map; it does not author a second. (`SIGNAL_CAPTIONS`
    is the assistant's contribution to the lift — fold it into the canonical map's signal entries.)
  - `AssistantPanel.tsx` + its `api.ts` type + a `postJson` call (`api.ts:583-593` is the existing POST
    path — reuse it): summonable, non-blocking, renders the answer with its provenance caption, a thinking
    state, and a loud error state. Numbers in any rendered citation go through `lib/format.ts`.
  - Wired into `Market.tsx`: the panel reads the **same** `(index, effectiveAsOf, mode)` tuple (`:26`,
    `:46`) and posts it with the question, so the assistant and the page can never describe different
    frames. **This edit is in the owner's exclusive frontend lane** ([frontend-is-owner-owned]) — the fleet
    ships the BFF + a stubbed `AssistantPanel` against a fixture (reading the canonical `lib/explain.ts`);
    the `Market.tsx` mount and final React polish are the owner's.
- Tests both sides.

## Depends on / coordinates with

- **Shares the coverage contract** with [MAT-LEGIBILITY-coverage-headline]: the facts block's coverage
  numbers are the *same* `option_rows/two_sided/excluded/two_sided_fraction` that spec puts on
  `/api/analytics`. Compute once, read in both — do **not** fork the metric (note it on the TASKBOARD).
- **Shares the mode frame** with [MAT-LEGIBILITY-strict-indicative-mode]: when `mode=indicative` the facts
  block is the indicative recompute's numbers, tagged `INDICATIF`, and the assistant must carry that tag.
  Land the headline + indicative specs (or at least their contracts) first — the assistant grounds on what
  they expose. The provenance words must line up with [MAT-LEGIBILITY-quarantine-drilldown]'s reason
  taxonomy (`missing_side`, `one_sided`, …) so the assistant's explanation of "why excluded" matches the
  drilldown's.
- **Shares the explanation map** with the P5 ⓘ/tooltip work ([frontend-design-language-2026-examples]
  Principle 5 / the shortlist item 3). The map is **owned by [MAT-LEGIBILITY-explanation-map]**
  (`lib/explain.ts`); this spec **consumes** it (and contributes `SIGNAL_CAPTIONS`). The tooltip work reads
  the same one — built so a tooltip can read it with no assistant in the loop.
- **No look-ahead.** The grounding builder reads only the requested/resolved date's store partitions, never
  a later date (mirror `analytics.py`'s `TradeDateDep` resolution and `coverage.py`/`health.py`). Default
  resolves to the latest date *with data*.
- **Shared-tree:** the `Market.tsx` mount overlaps the live `frontend-cockpit-ux` lane and is owner-owned —
  claim the row, serialize the one mount edit. The new BFF router + `grounding.py` + OpenRouter client +
  `AssistantPanel.tsx` are otherwise disjoint (`lib/explain.ts` is explanation-map's, consumed here).

## What to do (ordered)

1. **Explanation map first (cheap, unblocks both this and P5) — but it is [MAT-LEGIBILITY-explanation-map]'s
   deliverable, not a second copy.** Land or reuse the canonical `lib/explain.ts`; move the scattered
   head/hint constants (incl. `SIGNAL_CAPTIONS`) into it; have `charts.tsx`/`TenorPanel.tsx`/
   `Scorecards.tsx`/`api.ts` import from it. No behaviour change — pure centralisation, pinned by the
   existing component tests. If explanation-map is already landed, this step is "verify the assistant reads
   that map", not "create one".
2. **BFF grounding builder.** `grounding.py::build_grounding_context(ctx, underlying, trade_date, run_id,
   mode)` → `GroundingContext` (frame + facts block), reading the same partitions as `analytics.py`,
   formatting every number through the server-side `sci`/`sciUnit` mirror, reusing the shared coverage
   field. Pydantic-typed; additive-nullable where a field may be absent (older payloads).
3. **OpenRouter client + `/api/assistant` router.** Thin client reads `OPENROUTER_API_KEY`/`ASSISTANT_MODEL`
   from env. The router builds the context, composes the grounded prompt, calls OpenRouter, **validates the
   answer's numbers against the facts block**, and returns `{answer, citations, grounded, frame}`. Model
   error → labelled non-500. Register in `app.py`.
4. **Front panel.** `AssistantPanel.tsx` + `api.ts` type + `postJson` call; thinking/answer/loud-error
   states; provenance caption; numbers via `lib/format.ts`. Stub against a fixture so it ships without the
   owner's mount.
5. **Mount in `Market.tsx`** off the shared `(index, effectiveAsOf, mode)` tuple — **owner's lane**;
   coordinate, don't start a concurrent `Market.tsx` edit.
6. **No look-ahead; no secret leak.** Grounding reads only the resolved date; the key never reaches the
   bundle. Assert both.

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **Grounding fidelity — the central test.** For a fixture `(underlying, as_of)` with known store rows,
  assert the `GroundingContext` facts block contains the **same** ATM IV / skew / coverage fraction the
  scorecards + coverage headline render (hand-derive the expected formatted strings from `sci`/`sciUnit`,
  not from the builder). A number on the screen that is *absent* from the facts block is a bug.
- **Never-invents — the guardrail test.** With a **stubbed OpenRouter** returning a number that is **not**
  in the facts block, assert the router flags it: the response is `grounded=false` (or the offending number
  is rejected) and the surfaced answer is the honest-gap copy, **not** the fabricated number. This is the
  test that proves the anti-pattern can't happen — it must exist and be green.
- **Strict/indicative honesty.** `mode=indicative` → the facts block is tagged `INDICATIF` and the
  assistant's answer about a filled-in mark carries the tag; assert it never labels an indicative mark as
  "la clôture stockée". `mode=strict` (default) → no indicative framing.
- **Close instant.** For SX5E the frame's close instant is **17:30 CET**, not 22:00 — assert against the
  resolver, not a hard-coded string.
- **No secret in the bundle.** A test (or a CI grep) asserts `OPENROUTER_API_KEY` never appears in
  `web/dist` and the browser only ever calls `/api/assistant` (never OpenRouter directly).
- **No look-ahead.** A request for past date D ignores a later date's partitions (inject one; assert the
  facts block unchanged). `check-lookahead-bias` clean on the grounding read path.
- **Explanation-map single-source.** A test asserts the lifted constants (`SURFACE_LABEL`, `SMILE_HEAD`,
  the convexity gloss, the scorecard hints, `SIGNAL_CAPTIONS`) now resolve from the canonical `lib/explain.ts`
  (the existing component tests for those strings stay green — proves no copy drifted).
- **Front (component test).** Panel renders thinking → answer-with-provenance → loud error across the three
  states; an answer's provenance caption matches the active `(index, as-of, mode)`; a `grounded=false`
  response renders the honest-gap copy in alert/quiet tone, not as a confident number. Assert user-visible
  text + role.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` **and** the
  web suite (`tsc + lint + vitest + playwright`). The OpenRouter call is **always stubbed** in tests — no
  network, no key, in CI.

## Done criteria

Onglet 1 has a summonable, non-blocking assistant that answers "qu'est-ce que je regarde / c'est quoi ça /
comment je fais" in PM French/EN, grounded in the **same** data the page holds; every number it states is
lifted from a server-built facts block (the same store reads `/api/analytics` serves, formatted through the
house `sci`/`sciUnit` idiom) — it **provably cannot** state a number the screen never showed (the stubbed
out-of-facts test is green); it carries provenance (subject · 17:30-CET close · mode · coverage) on its
answers and respects the strict/indicative guardrail (indicative never read as the stored close); the
explanation copy is centralised once in the canonical `lib/explain.ts` ([MAT-LEGIBILITY-explanation-map]) and consumed by both the assistant and (later)
the ⓘ tooltip; the OpenRouter key lives server-side only and never reaches the bundle; the panel never goes
silent (thinking / answer / loud error); no look-ahead; both gates green.

## Gotchas

- **The guardrail is the point.** If the assistant can originate an analytics number, the task has failed
  even if it renders beautifully. Grounding is enforced in the data layer (facts block + answer
  validation), not in the model's goodwill — the system prompt is a backstop, not the guarantee.
- **Don't fork the coverage metric or the mode frame.** One coverage fraction (shared with the headline),
  one mode tag (shared with indicative). Read them; don't recompute.
- **Key server-side, always.** OpenRouter is called from the BFF. A browser-side call would leak the key
  and move grounding out of our control — both fatal. The front only ever hits `/api/assistant`.
- **Close instant is 17:30 CET** ([sx5e-close-instant-1730-cet]) — the assistant must say the OESX
  settlement instant, not the XEUR 22:00 futures close. The frame carries the resolved instant; the
  assistant never re-derives it.
- **Write the explanation copy once.** If the tooltip and the assistant read different strings, they will
  drift and the screen will contradict itself — exactly the defect this theme exists to kill. One
  canonical `lib/explain.ts` ([MAT-LEGIBILITY-explanation-map]), two consumers.
- **`Market.tsx` is the owner's lane** ([frontend-is-owner-owned]). Ship the BFF + map + stubbed panel; the
  mount and React polish are the owner's. Don't run a concurrent `Market.tsx` agent.
- **Reuse the design system + taxonomies.** `QcBadge` tones, `lib/format.ts`, the failure idiom from
  `AsyncBlock`/`GlobalErrorBanner`, provenance words that line up with the quarantine reasons — no new
  accent, no second vocabulary.
