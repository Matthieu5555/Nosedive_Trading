# MAT-LEGIBILITY-build-order — sequence the legibility cluster, partition file ownership, lock the shared contracts

> **What this is.** The eight legibility specs were written in parallel and converge on a handful of shared
> seams — one explanation map, one skeleton primitive, one feedback idiom, one coverage fraction, one
> close-instant rule. Written independently, three of them each named *their own* copy of the same artifact
> under a different file name and shape. This doc is the single place that says **which name wins, who owns
> each file, and in what order the work lands** so two implementation agents never build the same primitive
> twice or fork a contract. It is the editor-in-chief's reconciliation, not new scope: every spec keeps its
> substance; this resolves where they would otherwise collide. Each affected spec carries a short
> **"Cross-spec reconciliation"** note at its top pointing back here.
>
> Read alongside: [frontend-design-language-2026] (the seven principles), [frontend-design-language-2026-examples]
> (the same, anchored to `apps/frontend/web/src` `file:line`), and the three sibling feature specs
> [MAT-LEGIBILITY-coverage-headline] / [MAT-LEGIBILITY-quarantine-drilldown] / [MAT-LEGIBILITY-strict-indicative-mode].

---

## Standing constraints (apply to every row below)

- **Frontend is owner-owned** (`frontend-is-owner-owned`): `apps/frontend/web/**` is Matthieu's exclusive
  lane. The fleet ships **specs + additive read-only BFF fields** only; the React/TSX edits are the owner's
  to apply or explicitly delegate. Every front mount edit (`Market.tsx`, `charts.tsx`, `Scorecards.tsx`,
  `TenorPanel.tsx`) is claimed on `tasks/TASKBOARD.md` and serialized — never landed unannounced.
- **One coverage fraction, computed once.** `option_rows / two_sided / excluded / two_sided_fraction`
  ([MAT-LEGIBILITY-coverage-headline]) is the **only** coverage metric. Self-describing, explanation-map
  (`surface_coverage`), assistant (facts block), and strict-indicative (the active mode's numerator) all
  **read** it. Recomputing a second fraction anywhere is a defect.
- **Close instant is 17:30 CET** (OESX settlement, `sx5e-close-instant-1730-cet`) — never 22:00, never a
  hard-coded literal; resolved from the index registry. Every spec that prints an as-of obeys this.
- **No look-ahead** on any read path; every as-of is the *resolved* date only (`check-lookahead-bias`).
- **One design system** (Principle 7): `QcBadge` tones + `lib/format.ts` (`sci`/`sciUnit`/`UNITS`) +
  the locked Onglet-1 reading model are the vocabulary. No new accent per feature.
- **Gate:** the web suite (`npm run lint && npm test` = tsc + ESLint + Vitest) plus opt-in Playwright
  (`npm run e2e`) when a page/route/shared-layout is touched; the root gate
  (`uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`) only when a BFF field
  is touched. `apps/frontend/README.md` is the how-to.

---

## The shared artifacts — canonical names (this is the law the specs defer to)

These three were each named differently in ≥2 specs. The canonical name and **sole owner** are fixed here;
every other spec is a **consumer** and was edited to say so.

| Artifact | Canonical name + shape | Sole owner (author) | Consumers (read only) | Aliases collapsed |
|---|---|---|---|---|
| **Explanation map** | `lib/explain.ts` — `EXPLAIN: Record<id, ExplainEntry>`, `ExplainEntry { label, whatIs, howToRead, unit, whereFrom(ctx) }`, accessor `explainWithContext(id, ctx)` | [MAT-LEGIBILITY-explanation-map] | guidance (ⓘ glosses + selector ids), assistant (facts/provenance + `SIGNAL_CAPTIONS`), action-feedback (stage labels + button gloss), self-describing (the static how-to-read clause) | `lib/help.ts`/`HELP`/`helpFor`/`{title,body}` (guidance); `explanations.ts`/`{what,howToRead,provenance}` (assistant) |
| **ⓘ tooltip primitive** | `components/InfoDot.tsx` (looks up `EXPLAIN[id]`; hover **and** click/focus; non-modal; `aria-describedby`; renders `null` on unknown id) | [MAT-LEGIBILITY-explanation-map] | guidance (mounts it), assistant (may reuse for element glosses) | second `InfoDot` in guidance |
| **Skeleton loading state** | `components/ChartSkeleton` + the **`AsyncBlock` loading-branch swap** (footprint-preserving, `role="status"`, *"Chargement…"*, reduced-motion-gated) **incl. the `SKELETON_DELAY_MS ≈ 1 000 ms` `<1 s` floor + optional subject name** | [MAT-LEGIBILITY-skeletons] | action-feedback (consumes + relies on the delay floor), design-system ("one feedback idiom" routes ops through the same `AsyncBlock`) | second `<ChartSkeleton>`/`AsyncBlock` edit in action-feedback; "extend AsyncBlock with a skeleton" in design-system |

**Field-name map (so the aliases provably collapse, never fork):**
`title` ≙ `label`; `body` ≙ `whatIs` + `howToRead`; `what` ≙ `whatIs`; `provenance` ≙ `whereFrom(ctx)`.
`helpFor(id)`/`explanations` lookups ≙ `EXPLAIN[id]` / `explainWithContext`.

**Metric-id vocabulary (one set, snake_case):** `atm_level`, `term_structure_slope`, `iv_rank`,
`skew_25d`, `rv_minus_iv`, `rho_bar`, `convexity_25d`, `nappe`, `smile`, `greek_profiles`,
`surface_coverage`, plus guidance's selector ids `index-selector` / `as-of-selector`, plus action-feedback's
`launch-run` (button gloss) and the capture stage labels. Earlier dotted (`scorecard.rv-iv`), colon
(`scorecard:atm`, `tenor:convexity`), and hyphen (`convexity-25d`) ids were reconciled onto this set in the
respective specs.

**Provenance / reason taxonomy (one set, shared three ways):** a row excluded by strict as `missing_side`
is the *same* row indicative includes as a `one_sided` mark and the *same* phrase the ⓘ tooltip glosses —
`observed_two_sided | one_sided | last` (per-point, [MAT-LEGIBILITY-strict-indicative-mode]) line up with the
quarantine reasons (`missing_side | crossed | non_positive_bid | …`, [MAT-LEGIBILITY-quarantine-drilldown])
and the French phrases in `lib/explain.ts`. One vocabulary; do not re-translate it per consumer.

---

## The one-state-drives-all-labels rule (who enforces it)

Principle 2b — *every label binds to live state; if two labels on one screen can contradict, the screen is
broken*. [MAT-LEGIBILITY-self-describing] is the **enforcer**: a single `describeSurface(state)` over the
`(index, effectiveAsOf, mode, coverage)` tuple writes the panel `<h2>`, the caption, the Plotly figure
label, the empty/error copy, and the point tooltip. Every other spec that puts a label on Onglet 1 feeds
*that* descriptor, it does not assemble a competing title:
- coverage-headline supplies the `coverage` clause (read, not recomputed);
- strict-indicative supplies the `mode` word + per-point provenance;
- explanation-map's `whereFrom(ctx)` reads the *same* tuple for its provenance sentence;
- the assistant's frame caption (`subject · close · mode · coverage`) is the *same* sentence, conversationally.
One tuple in, one sentence out, everywhere. The QA matrix row **2b.7** ("switching the index updates every
self-describing label in one paint") is the regression that proves it.

---

## Build order (foundations → features → assistant)

Sequenced so each artifact exists before its consumers, and so the single most-shared file (`AsyncBlock`,
`Market.tsx`, `charts.tsx`) is edited by **one** spec at a time.

### Wave 0 — foundations (land first; everything rides them)

1. **[MAT-LEGIBILITY-design-system]** — consolidate first so the others conform to *one* `QcBadge`, one
   palette source (`chartTheme.ts` derived from `:root`), one `clockTime`, one `Callout`, one `Metric`
   unit-law, and the `ui/` ADR. *Visible change: none* (pixel-identical healthy day). Touches shared
   primitives + dupe call sites only. **Does not** build the skeleton (that is Wave 1) — it consumes it.
2. **[MAT-LEGIBILITY-skeletons]** — the `<ChartSkeleton>` + `AsyncBlock` loading-branch swap + reduced-motion
   CSS + never-blank test policy, **including the `SKELETON_DELAY_MS` `<1 s` floor and subject-naming**
   action-feedback depends on. One edit to `AsyncBlock`; lifts every page. Land it before action-feedback so
   `AsyncBlock` is touched once.
3. **[MAT-LEGIBILITY-explanation-map]** — `lib/explain.ts` (map + `ExplainEntry` + `explainWithContext`) and
   `components/InfoDot.tsx`, by *lifting* the scattered inline copy. This is the seam guidance, the assistant,
   and the coverage tooltip all plug into; build it before any of them.

> Waves 0.1–0.3 are mutually independent in *data* but all touch owner-lane front files; serialize the
> `Market.tsx`/`charts.tsx`/`Scorecards.tsx` edits on the TASKBOARD. The BFF-only slices (none in Wave 0
> except where design-system is pure front) can run in parallel.

### Wave 1 — features (ride the foundations; can parallelize across disjoint files)

4. **[MAT-LEGIBILITY-self-describing]** — `describeSurface` + the dynamic titles/captions/axes. Consumes the
   coverage clause (coverage-headline) and the mode word (strict-indicative); degrades cleanly if either is
   absent. The enforcer of one-state-drives-all-labels.
5. **[MAT-LEGIBILITY-guidance]** — mounts `<InfoDot>` (from Wave 0.3) on the headings + convexity readout and
   adds the **`PulseHint`** next-step flash + the pulse/reduced-motion CSS. Adds `index-selector`/
   `as-of-selector` entries to `lib/explain.ts`. Owns no map, no `InfoDot`.
6. **[MAT-LEGIBILITY-action-feedback]** — BFF stage passthrough on `JobStatus`/`/api/jobs` + `<JobProgress>`
   + the backgroundable done/error notice. Consumes the skeleton (with its delay floor, from Wave 0.2) and
   authors the stage labels + launch-button gloss as entries in `lib/explain.ts` (Wave 0.3). Has the only
   BFF change in this wave (`runner.py`, additive-nullable).

> The three sibling feature specs ([…-coverage-headline], […-quarantine-drilldown], […-strict-indicative-mode])
> land **in/around Wave 1** — self-describing and the assistant depend on the coverage block and the mode
> frame they expose. Land their BFF contracts at the front of Wave 1 (or stub against the typed contract).

### Wave 2 — the assistant (flagship; rides everything)

7. **[MAT-LEGIBILITY-assistant]** — the BFF grounding builder + OpenRouter client + `/api/assistant` +
   `AssistantPanel.tsx`. Consumes `lib/explain.ts` (Wave 0.3), the coverage facts (coverage-headline), and
   the mode frame (strict-indicative). The numeric grounding lives in the **server-built facts block**; the
   front map supplies the prose. Highest-risk row — the never-invents guardrail (QA 6.2) must be green.

### Cross-cutting — runs continuously, gates the waves

8. **[MAT-LEGIBILITY-qa-strategy]** — `e2e/legibility.spec.ts` + the danger fixtures + the pair→test matrix.
   LIVE rows guard Wave 0/1/2 as they land; each feature spec flips its `test.fixme`→`test` **in the same
   commit** it ships. Owns test code + fixtures only, no product `src/`.

---

## File-ownership partition (so impl agents never collide)

Each file below is **created/edited by exactly one spec**; consumers import, they do not re-author. "Owner
lane" = an `apps/frontend/web` file Matthieu sequences; the fleet's slice is the spec + any additive BFF
field.

| File / artifact | Owned by | Consumed by | Lane |
|---|---|---|---|
| `lib/explain.ts` (`EXPLAIN`, `ExplainEntry`, `explainWithContext`) | explanation-map | guidance, assistant, action-feedback, self-describing | owner |
| `components/InfoDot.tsx` | explanation-map | guidance, assistant | owner |
| `components/ChartSkeleton` + `AsyncBlock` loading-branch swap + reduced-motion CSS + `SKELETON_DELAY_MS` floor | skeletons | action-feedback, design-system | owner |
| `src/test/assertNeverBlank` + README never-blank policy | skeletons | all async-surface tests | owner |
| `lib/surfaceDescriptor.ts` (`describeSurface`) + `closeInstant` helper | self-describing | — | owner |
| `charts.tsx` titles/axes/empty-copy/tooltip (`SURFACE_LABEL`→fn) | self-describing | — (explanation-map *moves the static clause* out; coordinate) | owner — serialize |
| `components/PulseHint.tsx` + pulse/motion CSS | guidance | — | owner |
| `components/JobProgress` + `Job` type stage fields (`api.ts`) | action-feedback | — | owner |
| `runner.py` `JobStatus` stage fields + `/api/jobs` passthrough (additive-nullable) | action-feedback | front `<JobProgress>` | **BFF — fleet-eligible** |
| `chartTheme.ts` `:root` derivation, `QcBadge`, `clockTime`, `Callout`, `Metric` unit-law, `ui/` ADR | design-system | every page | owner (primitives) + `.agent/decisions/` ADR |
| `grounding.py`, OpenRouter client, `routers/assistant.py`, `app.py` registration | assistant | front panel | **BFF — fleet-eligible** |
| `AssistantPanel.tsx` + its `api.ts` type | assistant | — | owner (mount) / fleet (stubbed panel against fixture) |
| `coverage` block on `/api/analytics`, `excluded_breakdown`, indicative recompute endpoint | the three sibling specs | self-describing, explanation-map (`surface_coverage`), assistant | **BFF — fleet-eligible** |
| `e2e/legibility.spec.ts`, `src/test/fixtures.ts` danger fixtures | qa-strategy | — | owner-authored e2e |

**The high-collision files** — `Market.tsx` (mounts from self-describing, guidance, assistant, and the three
siblings), `charts.tsx` (self-describing titles + explanation-map clause move), and `AsyncBlock.tsx`
(skeletons + action-feedback + design-system) — are each touched by multiple specs. The rule: **one spec
edits the file per landing**, claimed on the TASKBOARD; the others rebase onto its result. The build order
above is arranged so the shared-file edits are serialized (skeletons before action-feedback on `AsyncBlock`;
design-system primitives before everything; explanation-map's clause-move coordinated with self-describing's
title edit on `charts.tsx`).

---

## Acceptance criteria (this build-order doc is "done" when)

1. There is **exactly one** canonical name for each shared artifact (explanation map = `lib/explain.ts`;
   ⓘ = `components/InfoDot.tsx`; skeleton = `<ChartSkeleton>`+`AsyncBlock` swap), and every spec that earlier
   named its own copy carries a "Cross-spec reconciliation" note pointing here and identifying itself as a
   **consumer**, not an author.
2. The field-name aliases (`title`/`body`, `what`/`provenance`) and the metric-id schemes are mapped onto
   one shape and one snake_case id set — no spec can stand up a second, incompatible map.
3. The one-state-drives-all-labels rule has a single enforcer (self-describing's `describeSurface`), and
   every label-bearing spec is documented as feeding that descriptor, not assembling a rival title.
4. The shared contracts are stated once and marked read-only-for-consumers: one coverage fraction, the
   17:30 CET close instant, the `observed_two_sided | one_sided | last` ↔ quarantine-reason taxonomy.
5. A wave-ordered sequence (foundations → features → assistant, QA continuous) exists such that every
   artifact is built before its consumers, and the three high-collision files (`Market.tsx`, `charts.tsx`,
   `AsyncBlock.tsx`) are edited by one spec at a time.
6. A file-ownership table assigns each file/artifact to exactly one owning spec, marks the fleet-eligible
   BFF slices vs the owner-lane React files, and names the high-collision files with the serialize rule.
7. No spec's *substance* was rewritten — only contradictions were resolved, and each edit is listed in the
   "Edits made" log below.

---

## Edits made to the individual specs (reconciliation log — no substance changed)

- **explanation-map**: added a top reconciliation note declaring it the sole owner of `lib/explain.ts` +
  `components/InfoDot.tsx`, with the field-name alias table.
- **guidance**: added a reconciliation note (consumes `lib/explain.ts`/`InfoDot`, owns `PulseHint`);
  rewrote the Objective sentence, the two Owns bullets (map + `InfoDot`), and the two "What to do" steps that
  previously *created* `lib/help.ts`/`InfoDot` → now *consume* the canonical ones and contribute selector ids.
- **assistant**: added a reconciliation note (consumes `lib/explain.ts`); rewrote the Owns bullet and the
  "What to do" step 1 that previously *created* `explanations.ts` → now consume the canonical map; reconciled
  its metric ids onto the snake_case set and folded `SIGNAL_CAPTIONS` in as a contribution.
- **action-feedback**: added a reconciliation note (consumes skeletons' `<ChartSkeleton>` + contributes the
  `<1 s` delay floor; stage labels + button gloss go in `lib/explain.ts`); rewrote the `<ChartSkeleton>` Owns
  bullet and the two "What to do" steps (skeleton, button gloss) accordingly.
- **skeletons**: added a reconciliation note declaring it sole owner of the skeleton primitive/`AsyncBlock`
  swap and folding the `SKELETON_DELAY_MS` `<1 s` floor + subject-naming into *this* change (so the
  `AsyncBlock` loading branch is edited once, not twice).
- **design-system**: added a reconciliation note (consumes skeletons' skeleton; map = `lib/explain.ts`);
  rewrote the "one feedback primitive" Owns bullet and "What to do" step 4 to consume `<ChartSkeleton>`
  rather than build a second skeleton.
- **self-describing**: unchanged — it was already internally consistent and correctly reads (not forks) the
  coverage clause, the mode word, and the 17:30 CET close instant. It is named the one-state enforcer here.
- **qa-strategy**: unchanged — it already maps the pairs to tests and cites the retiring specs; its matrix
  rows align with the canonical names above (the `<InfoDot>`/skeleton/map rows point at the right owners).
