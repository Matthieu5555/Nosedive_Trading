# MAT-LEGIBILITY-action-feedback — every action explains itself, and every long job narrates

> **Owner ask (2026-06-17).** "When you click a button you should know what it does in the back end —
> and while it runs, you should *see* it run." Today a capture launch flips one button to
> *"Launching…"* and a job row from queued→running→done, with nothing in between: a 10s–to–minutes
> pipeline gives a PM a single coarse state and a free-text `message`, no determinate progress, no
> step the eye can follow, no way to step away and be told when it lands. And the panels on Onglet 1
> swap to a bare one-line *"Loading…"* that collapses the layout. This is Principle 4 of the design
> language made concrete: **feedback proportional to duration**, with the exact thresholds spelled out
> so they can't be misread. The owner's test stays the same — *can the PM tell what's happening, and
> would they ever be misled into thinking a job is done, stalled, or fine when it isn't?*

> **Cross-spec reconciliation (canonical — see [MAT-LEGIBILITY-build-order]).** Two shared primitives this
> spec leans on are **owned elsewhere**: (1) the **`<ChartSkeleton>` + the `AsyncBlock` loading-branch swap +
> reduced-motion CSS + the never-blank policy** are [MAT-LEGIBILITY-skeletons]'s deliverable — this spec
> **consumes** that skeleton and contributes only the **`<1 s` delay-floor gating (`SKELETON_DELAY_MS`)** and
> the **subject-naming** of the loading state, which skeletons must land together with the primitive (it is
> the `<1s` threshold from the design-language P4 table). (2) The **stage→PM-label map and the button gloss**
> are entries in the canonical explanation map **`lib/explain.ts`** ([MAT-LEGIBILITY-explanation-map]); author
> them **there**, not in a second "shared explanation map". What this spec uniquely owns is the **BFF stage
> passthrough** on `JobStatus`/`/api/jobs`, the **`<JobProgress>`** component, and the **backgroundable
> done/error notice**. Build order: skeletons lands `<ChartSkeleton>`+`AsyncBlock` (with the delay floor),
> then this spec wires `<JobProgress>` and the stage labels.

## What's true today (grounded in code)

- **The launch button is honest but shallow.** `RunControlPanel` flips
  `{launch.isPending ? "Launching…" : "Launch run"}` (`RunControlPanel.tsx:166`) and disables while
  pending (`canLaunch` `:117`); a failed launch is a labelled `role="alert"` (`:174-178`). Good — but
  the button reverts to idle the instant the POST returns `202`, **while the real work has only just
  started** on the server.
- **The job ledger is a coarse 4-state pill.** `JobsTable`/`JobRow` (`RunControlPanel.tsx:40-83`)
  render the state pill from `JOB_STATE_CLASS` (`:16-21`, queued/running both `ops-pill--warn`,
  done `ok`, error `bad`), plus `started/finished/message`. The empty case is honest
  (`:56-60`). There is **no percent, no current stage, no ETA** — `message` is one free-text line.
- **The server already knows the stages — it just doesn't expose them.** The runner sets only two
  coarse messages: `"Replaying the latest committed day into a surface…"` then
  `"Pipeline completed successfully"` (`runner.py:92,95`), and the `JobStatus` dataclass
  (`runner.py:37-59`) carries `state/started_at/finished_at/message/summary` — **no `stage`, no
  `progress`, no `total`**. But `build_surface` underneath emits real, named stages
  (`orchestration.surface.{start,universe,…}`, `surface_job.py:86,90,125`) and the EOD pipeline names
  five canonical stages — `universe_refresh · collection · analytics · reconciliation · qc`
  (`pipeline.py:21-25,98-121`). The narration the owner wants is a passthrough of stages the engine
  already walks, not new instrumentation.
- **`useJobs` already polls while a job is live.** `hooks/queries.ts:55-66` refetches `/api/jobs`
  every `JOBS_REFRESH_MS` (4 000 ms) **only while** some job is `queued`/`running`, then stops. The
  channel for live progress already exists; it just carries a state enum, not a stage+percent.
- **Loading on the Données side is a bare text, not a skeleton.** `AsyncBlock` (`AsyncBlock.tsx:9-25`)
  renders the literal `"Loading…"` in a one-line `state-panel` (`:10-14`); a 480px nappe pops in from
  one line of text and the layout reflows on every selector change (`Market.tsx:100-103`,
  `:123-126`). The `<1s/1–9s/10s+` thresholds aren't yet expressed anywhere.

## Objective

Make duration-proportional feedback a **designed, shared** behaviour across the cockpit, to the
exact thresholds the design language fixes:

| Duration | Pattern (normative) |
|---|---|
| **< 1 s** | **No spinner.** A loader that flashes for 300 ms is friction, not feedback. The surface either already shows content or shows the skeleton only once it has been pending past the threshold. |
| **~1–9 s** | **Skeleton** that reserves the panel's real footprint (no reflow), or a looped indeterminate bar for an action with no layout to hold. |
| **10 s +** | **Determinate** progress: `étape k/N` step tracker **with the real stage name** ("collecte de la chaîne d'options…", "résolution des IV…") and a percent. Never a generic bar with no words. |
| **minutes** | **Backgroundable.** The user works elsewhere; the job ledger is the persistent truth; a non-blocking done/error notice fires when it lands. |

For the **capture run** (the one true long job, 10s–minutes), surface the engine's stages as a
**step tracker on the running job row** — *"étape 2/4 · collecte de la chaîne…"* — so a PM watches the
pipeline walk instead of staring at a frozen `running` pill. For **Onglet-1 fetches** (sub-second to a
few seconds), replace the bare `"Loading…"` with a **footprint-preserving skeleton** that names the
subject it is about to fill. And give **every action button a one-line gloss of what it does in the
back end** (Principle 4, first half), reusing the explanation-copy habit.

## Design intent (this is a designed behaviour, not a spinner)

The locked Onglet-1 reading model (`frontend-page1-reading-model`), the Operations job-ledger layout,
the `QcBadge`/`ops-pill` tone palette, and `lib/format.ts` are **not** to be reinvented (Principle 7 —
spend boldness once). This is surfacing, in the existing type/colour system, that already pass the
owner's *"qu'est-ce qui se passe ?"* test.

- **Threshold-keyed, never time-on-a-timer-you-invent.** The `<1s` rule is the load-bearing one: do
  **not** mount a skeleton/bar the instant a fetch starts — mount it only once pending crosses ~1 s
  (`SKELETON_DELAY_MS`). A skeleton that flashes for 200 ms on a warm cache is exactly the friction the
  threshold forbids. Below the floor, show nothing new; the content arrives faster than a loader could.
- **Determinate means honest.** A percent or `k/N` is a **claim** about progress; it must track a real
  stage count from the server, never a fake CSS animation creeping to 90% and parking. If the server
  can't say which stage, the bar is **indeterminate** (looped), not a fabricated percent — silent-green
  honesty (`frontend-no-silent-failures`) applied to progress. A `print`/label is not proof
  (`AGENTS.md`): the stage shown is the stage the job has *reached*, set after the stage starts, never
  before.
- **Step name in PM register** (`analytics-pm-legible-framing`): "collecte de la chaîne d'options",
  "résolution des volatilités implicites", "ajustement de la nappe", "contrôle qualité" — never
  `STAGE_COLLECTION`, `solve_iv`, `build_surface`, `run_id`. Map the engine's enum to plain words once,
  in one place, the way `statusLabel` (`format.ts:163-166`) already de-snakes a state.
- **Backgroundable = the ledger is the truth, and done is announced.** Launching does **not** block the
  page; the user can change tabs. When the job lands, a **non-blocking** notice (a transient toast / an
  `aria-live` line on the panel, not a modal) says *"Capture SX5E terminée — nappe prête"* or, on
  failure, the loud red the house already uses (`role="alert"`, like `:174-178`). Never a silent
  done-pill the PM has to go hunting for.
- **Intent legible before the click** (Principle 4, first half): the launch button carries a one-line
  gloss of the backend action — *"Rejoue le dernier jour capturé en une nouvelle nappe — n'écrit rien
  sur le disque tant que ce n'est pas validé"* (true: the sample run builds with `persist=False`,
  `runner.py:142`). No mystery verb. The gloss copy lives where the future ⓘ-tooltip + assistant can
  read it (shared explanation map, the P5/P6 seed) — write it once.
- **Never a frozen-looking job** (`frontend-no-silent-failures`): a `running` pill that hasn't changed
  in minutes reads as "stuck". The step tracker is what tells the PM it's alive; if no stage update has
  arrived within a heartbeat the row says *"en cours…"* (indeterminate), it does not pretend to a
  percent.

## Owns

- **BFF — stage passthrough on the job model.** Add `stage: str | null`, `stage_index: int | null`,
  `stage_total: int | null` to `JobStatus` (`runner.py:37-59`) and its `to_dict` (`:49-59`). In
  `_run_job` / `_build_sample_surface` (`runner.py:86-161`), set the stage **as each engine stage
  starts** (the stage names already exist — `surface_job.py:86,90,125`, `pipeline.py:21-25`). Map the
  engine enum → a PM-register label in **one** helper (BFF side or front side, but once). `to_dict`
  stays additive-nullable so an in-flight older payload still parses. `/api/jobs` + `/api/jobs/{id}`
  (`routers/run.py:65-79`) carry the new fields automatically (they serialise `to_dict`).
- **Front — the shared feedback primitives.**
  - The **`<ChartSkeleton>` + `AsyncBlock` loading-branch swap is [MAT-LEGIBILITY-skeletons]'s primitive**
    (do not build a second). This spec contributes to it the **`<1 s` delay-floor gating**: the skeleton
    **only mounts after `SKELETON_DELAY_MS` (~1 000 ms)** of pending (replacing the bare `"Loading…"`,
    `AsyncBlock.tsx:10-14`), and **names its subject** (*"Chargement de la nappe SX5E au 2026-06-17…"*, not
    a generic word — self-describing, §2b). Coordinate so these two behaviours land **with** the primitive
    in skeletons, not as a divergent fork here.
  - A `<JobProgress>` rendered in the running `JobRow` (`RunControlPanel.tsx:40-53`): a determinate
    `étape k/N · {stage label}` + percent when `stage_index`/`stage_total` are present; an
    indeterminate looped bar + *"en cours…"* when they're null. Extend the `Job` type
    (`api.ts:90-99`) with the three nullable fields.
  - A non-blocking done/error notice driven off the job transitioning to `done`/`error` in the
    `useJobs` poll (`queries.ts:55-66`) — `aria-live="polite"` for done, `role="alert"` for error.
  - A one-line action gloss on the launch button (title/hover today; the shared explanation map later).
- Tests both sides.

## Depends on / coordinates with

- **Reuses the existing poll** (`useJobs`, `queries.ts:55-66`) — no websocket, no new transport. The
  4 s poll already runs only while a job is live; the new fields ride the same `/api/jobs` payload.
- **Shares the de-jargon habit with the explanation map** the legibility cluster is converging on
  (P5/P6 seed in `frontend-design-language-2026-examples.md`): the stage→PM-label map and the button
  gloss are the *first* entries of that shared "what is this / what does this do" copy — write them so
  the ⓘ-tooltip and the assistant can consume the same strings. **Do not fork** the copy into two
  places.
- **Shared-tree:** the `RunControlPanel.tsx`/`runner.py` edits overlap any live Operations lane and the
  `AsyncBlock` edit touches every page that loads — claim the rows on `tasks/TASKBOARD.md` and
  coordinate the `AsyncBlock` signature change (it is imported widely: `Market.tsx`, `RunControlPanel`,
  and others). The new `<ChartSkeleton>`/`<JobProgress>` components are disjoint.
- Sibling of the three [MAT-LEGIBILITY-coverage-headline]/[…-quarantine-drilldown]/[…-strict-indicative-mode]
  specs — those prove Principles 1–3+7; this opens Principle 4. It is independent of them and can ship
  in parallel.

## What to do (ordered)

1. **BFF stage passthrough.** Add the three nullable stage fields to `JobStatus` + `to_dict`
   (`runner.py:37-59`). In `_build_sample_surface` (`runner.py:119-161`) set `stage`/`stage_index`/
   `stage_total` as the build walks (start of universe/collection/IV-solve/fit — the engine already
   logs these). One helper maps the engine stage id → a PM-register French label. Absent/unknown stage
   → all three `null` (the front falls back to indeterminate). Never let stage-setting throw into the
   job boundary (`:99-102`).
2. **Front job progress.** Extend `Job` (`api.ts:90-99`) with the three nullable fields. Add
   `<JobProgress>`; render it in the running `JobRow`. Determinate `étape k/N · {label}` + percent when
   present; indeterminate + *"en cours…"* when null. A `done` row shows the final summary, never a stale
   bar; an `error` row shows the message loud.
3. **Skeleton with the `<1s` floor.** Use [MAT-LEGIBILITY-skeletons]' `<ChartSkeleton>` + `AsyncBlock`
   swap; add to it the `SKELETON_DELAY_MS` gating (skeleton mounts only after ~1 000 ms pending; below the
   floor render nothing new, keep prior content / empty footprint) and the subject-naming. Land this delay
   floor **in the skeletons spec's change**, not as a parallel skeleton. Wire it on the Onglet-1 panels
   (`Market.tsx:100-103,123-126`).
4. **Backgroundable notice + button gloss.** A non-blocking done/error notice off the `useJobs`
   transition. A one-line back-end gloss on the launch button (`RunControlPanel.tsx:160-167`), copy
   authored as an entry in the canonical `lib/explain.ts` ([MAT-LEGIBILITY-explanation-map]), not a second
   map.
5. **No look-ahead / no fabrication.** Progress reflects only stages the job has *reached*; never a
   timer-driven fake percent, never a stage set before it starts.

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **BFF stage passthrough — independent oracle.** Drive a sample job through the runner with a stubbed
  build that reports a known stage sequence (e.g. 4 stages); assert `to_dict` exposes
  `stage_index`/`stage_total` matching the **hand-written** expected sequence and that `stage` is the
  PM-register label, not the engine enum. A job that never reports a stage → all three `null`,
  `state` still transitions correctly. A build that raises mid-stage → `state="error"`, message set,
  stage fields don't lie about completion.
- **Additive/back-compat.** A `/api/jobs` payload **without** the new fields parses on the front (type
  is nullable); the running row degrades to the indeterminate bar, not a crash.
- **Front `<JobProgress>` (component test).** Determinate payload (`stage_index=2,stage_total=4`)
  renders *"étape 2/4"* + the stage label + the right percent (assert **visible text + role**, not
  internal state); null-stage payload renders the indeterminate *"en cours…"* with **no** percent;
  `done`/`error` rows render their terminal copy, not a bar.
- **Skeleton threshold (component test, fake timers).** Pending for 300 ms → **no** skeleton in the DOM
  (the `<1s` rule); pending past `SKELETON_DELAY_MS` → the subject-naming skeleton appears, holding the
  panel footprint; resolves → content, no reflow assertion failure. This is the one most likely to be
  skipped — it is the acceptance criterion, build it.
- **Backgroundable notice.** A job poll transitioning running→done emits a `polite` notice;
  running→error emits a `role="alert"`; neither blocks (no modal/focus-trap in the tree).
- **Playwright (extend the e2e).** Launch a sample run; assert the running row shows a step tracker (not
  a frozen pill) and that the page stays interactive (navigate away and back; the ledger still reflects
  the job). See `apps/frontend/README.md`.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` **and**
  the web suite (`tsc + lint + vitest + playwright`).

## Done criteria

`/api/jobs` carries additive-nullable `stage`/`stage_index`/`stage_total` sourced from the stages the
engine already walks (no new instrumentation); the running capture row shows a **determinate step
tracker with the real stage in PM French** when the engine reports it, and an honest indeterminate
*"en cours…"* when it doesn't — never a fabricated percent; the launch is backgroundable with a
non-blocking done/error notice and never a silent done-pill; Onglet-1 fetches show a
footprint-preserving, subject-naming skeleton that **only** appears past the ~1 s floor (sub-second
fetches show no loader); every action button states what it does in the back end in one line, authored
in the shared explanation map; no look-ahead, no fake progress; both gates green.

## Gotchas

- **Surface the stages, don't invent a pipeline.** The stage names exist (`surface_job.py:86-125`,
  `pipeline.py:21-121`); pass them through. If the runner can't yet learn a stage from the build, the
  field is `null` and the bar is indeterminate — that is honest. Do **not** ship a CSS timer that fakes
  a percent.
- **Respect the `<1s` floor — it is the whole point of "no spinner under a second".** A skeleton that
  flashes on a warm cache is a regression, not a feature. Gate the mount on `SKELETON_DELAY_MS`.
- **One feedback vocabulary.** Don't add a third loading idiom: `AsyncBlock` (Données side) and the
  `ops-pill`/react-query idiom (Operations side) are already two (flagged in the examples doc, P7) —
  this task should *converge* them on the shared skeleton/progress primitives, not add a fourth accent.
- **Don't fork the copy.** The stage→label map and the button gloss are the first rows of the shared
  explanation map (P5/P6). Author them once so the tooltip and assistant can read the same strings.
- **Backgroundable ≠ silent.** Letting the user leave is the goal; leaving them *uninformed* when it
  lands is the original sin. Done and error both announce, at the right altitude (polite vs alert).
- **The runner is in-memory and per-process.** `PipelineRunner.jobs` is a process dict (`runner.py:65`);
  progress lives only as long as the BFF process. That's fine for v1 (matches today's behaviour) — don't
  over-engineer persistence; just don't claim durability the code doesn't have (`AGENTS.md` honesty bar).
