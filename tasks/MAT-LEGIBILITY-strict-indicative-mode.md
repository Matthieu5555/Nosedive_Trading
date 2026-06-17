# MAT-LEGIBILITY-strict-indicative-mode — let the PM choose strict vs indicative, and never confuse them

> **Owner ask (2026-06-17).** Strict (two-sided only) is the right *default* and the only thing that
> should ever be stored or traded on — it is the guard the 2026-06-15 canary proved we need. But
> strict-*only* is not universally right: intraday, and in the far wings / long tenors, a one-sided or
> last-based mark is sometimes the only information that exists, and excluding all of them makes the
> surface emptier (then it extrapolates over the gap — sometimes worse than a caveated mark). A PM
> should be able to ask for "your best estimate including those marks" — **as long as they always know
> which one they're looking at.** The danger is never "which is better"; it is letting the two be
> confused. That is exactly what bit us on the 15th.

## ⛔ Hard guardrail (load-bearing — do not weaken)

**Strict stays the default, the canonical, and the *stored* surface.** Indicative is an
explicitly-badged, view-time *overlay* that **never** silently replaces it and is **never** persisted
or read by any strategy/booking path. If indicative ever becomes the thing on disk or the thing a
strategy reads, the canary hole is reopened. The stored close stays strict (`driver.py` →
`qc_results`/`surfaces`); indicative is a recompute-on-request view only. This guardrail is the reason
the task exists; encode it in the BFF (indicative is a separate, clearly-typed response that does not
overwrite the strict surface) and in the UI (badge + default).

## What's true today (grounded in code)

- The fit is **strict-only and has no mode switch**: `driver.py::_build_iv_points` (`:487`) drops every
  snapshot failing `_has_two_sided_option_quote` before `solve_iv`; `_build_surfaces` fits only those.
  There is **no permissive path anywhere** — building indicative is **net-new compute**, not a flag flip.
- `/api/analytics` serves one (strict) surface; no mode parameter, no badge, no second surface.
- The web nappe/smile (`charts.tsx`, `Market.tsx`, `TenorPanel.tsx`) renders that one surface with no
  indication that a different inclusion rule would change it.

This is the **biggest** of the three legibility tasks and the only one that adds real modelling +
compute. Land [MAT-LEGIBILITY-coverage-headline] and [MAT-LEGIBILITY-quarantine-drilldown] first — they
are pure surfacing; this one needs a data contract and an engine path.

## ⚠️ One open modelling decision (resolve before building the engine path)

**What does "indicative" actually price for a contract with no two-sided quote?** Options:
(a) use `last` where there's no two-sided quote; (b) use the one available side (bid-only / ask-only)
as the mark; (c) both, with a precedence rule. This determines whether we compute *one extra* surface
or several, and how honest the result is. **This is the owner/quant call**, not a frontend choice — the
front consumes whatever the contract defines. Bring it to the owner with the data-contract draft (see
step 1) rather than guessing. Recommended starting point for discussion: (b) one-sided mark with an
explicit per-point provenance tag, `last` only as a last resort and tagged as such — but confirm.

## Objective

A **Strict ⟷ Indicative** mode toggle on Onglet 1 that re-renders the nappe + smile + greeks under the
chosen inclusion rule, with the indicative view **unmistakably badged** as indicative (not the stored
close), and per-point provenance available so a PM can see *which* marks are real two-sided quotes vs
filled-in. Strict is the landing state every time.

## Design intent (the toggle's whole job is to prevent confusion)

- **The badge is the signature element.** Indicative mode must change the page's *frame*, not just a
  number — a persistent, unmissable "INDICATIF — pas la clôture stockée" marker on the nappe and smile
  whenever indicative is active, in the design system's warning tone (reuse the `QcBadge` family; no new
  accent). Strict mode shows no badge (absence = canonical). Spend the boldness here.
- **Default + memory.** Loads in **Strict** every time (canonical-first); the toggle is a deliberate
  act, and switching is visibly a mode change (the badge appears, the coverage headline updates its
  numerator), not a silent data swap.
- **Provenance is visible on demand.** In indicative mode, filled-in points are distinguishable from
  real two-sided points (e.g. a marker style / muted rendering on the smile, or a per-point tag in the
  greeks table) — so "best estimate" never masquerades as "observed". This is where indicative earns
  its keep over plain extrapolation.
- **Ties into the coverage headline.** Strict: "Nappe sur 1 706 / 2 412". Indicative: "Nappe sur
  2 280 / 2 412 — 574 marques indicatives". Same honest read, recomputed for the active mode — the two
  tasks share the coverage contract.
- **Legibility over cleverness** (`analytics-pm-legible-framing`): the toggle says what it does
  ("Strict — deux-faces seulement" / "Indicatif — inclut les marques à une face"), the consequence is
  shown, not sold.

## Owns

- **Engine (infra)**: a permissive IV-point inclusion path parallel to strict — `_build_iv_points`
  gains a typed inclusion policy (strict = today's behaviour, byte-identical default), and a
  recompute-on-request entry that builds an indicative surface **without writing it**. Per-point
  provenance (`observed_two_sided` | `one_sided` | `last`) on the indicative points. **The persisted
  daily close path does not change** (strict stays the only stored surface).
- **BFF**: `/api/analytics?mode=strict|indicative` (default strict). Indicative is a recompute over the
  resolved date's raw quotes returning a **separately-typed** surface + per-point provenance + its own
  coverage block; it never mutates or shadows the stored strict surface. Serializer.
- **Front**: the mode toggle + the indicative badge + provenance rendering on nappe/smile/greeks +
  `api.ts` types; tests.

## Depends on / coordinates with

- **Hard-depends on the open modelling decision above** — do not build the engine path until the
  owner picks what indicative prices. The frontend toggle + badge can be specced/stubbed against a
  fixture in parallel, but the real contract waits on that call.
- **Shares the coverage contract** with [MAT-LEGIBILITY-coverage-headline] (the headline must report
  the active mode's numerator) and the provenance taxonomy is adjacent to
  [MAT-LEGIBILITY-quarantine-drilldown]'s reason taxonomy — keep them consistent (a row excluded by
  strict for `missing_side` is the same row indicative includes as a `one_sided` mark; the words should
  line up).
- **Risk/Stress lane interaction:** indicative is **view-only on Onglet 1**. It must not flow into the
  book/stress/attribution path (Onglet 2) or any strategy read — those stay on the stored strict
  surface. State this explicitly so a later integration doesn't quietly wire indicative into reprice.
- **Shared-tree:** touches `driver.py` (infra), the analytics router (BFF), and `Market.tsx`/
  `charts.tsx`/`TenorPanel.tsx` (front). This spans three lanes — split into per-lane slices that link
  this spec (engine policy → BFF mode → front toggle), claim each, and serialize the front edits behind
  the live `frontend-cockpit-ux` lane. Do **not** start the page-1 front slice concurrently with another
  Market.tsx agent.

## What to do (ordered)

1. **Data contract + owner decision (no code).** Draft the indicative contract: what it prices (the
   open decision), the per-point provenance enum, the separate response shape, and the
   strict-stays-canonical rule in writing. Get the owner's pricing call. Land it as a short ADR
   amendment if it changes the surface contract (blueprint stays the amendable source).
2. **Engine inclusion policy.** Add the typed strict|indicative inclusion policy to `_build_iv_points`;
   strict default is byte-identical (pin with a golden/regression test on the stored close). Add the
   non-persisting indicative recompute with per-point provenance. **Do not touch the daily-close write.**
3. **BFF `mode` param.** `mode=strict` (default) returns today's payload unchanged; `mode=indicative`
   returns the recomputed indicative surface + provenance + its own coverage block, separately typed.
4. **Front toggle + badge + provenance.** Default strict; toggling shows the unmissable indicative
   badge across nappe/smile, updates the coverage headline numerator, and renders filled-in points
   distinguishably. Reuse `QcBadge` tones; no new palette.
5. **No look-ahead; no persistence.** Indicative reads only the resolved date's raw quotes, writes
   nothing. Assert the stored strict surface is byte-identical with the feature present.

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **Strict unchanged — regression pin.** With the inclusion policy added, the stored daily-close
  surface for a fixture date is **byte-identical** to pre-change (golden). The persisted close path
  writes nothing indicative.
- **Indicative includes the right rows — independent oracle.** A fixture chain with N two-sided + M
  one-sided rows → indicative surface is built on N+M points (per the chosen pricing rule), each tagged
  with the expected provenance; strict on N. Hand-derive N, M and a sample point's expected mark from
  the rule, not the code.
- **Provenance honesty.** Every indicative point carries a provenance tag; no `observed_two_sided` tag
  on a row that was one-sided in raw (assert against the fixture's known sides).
- **BFF mode routing.** `mode` absent/`strict` → unchanged payload; `mode=indicative` → separately-typed
  surface that does not appear in or overwrite the strict response; coverage block reflects the active
  mode's numerator.
- **Front (component test).** Strict load shows **no** indicative badge; toggling to indicative shows
  the badge on nappe + smile, updates the coverage headline copy, and marks filled-in points. Assert
  user-visible text/markers.
- **No leak into risk path.** A test asserts the book/stress/attribution read path resolves the strict
  surface regardless of any indicative request (guard the guardrail).
- **No look-ahead** on the indicative recompute; `check-lookahead-bias` clean.
- Gate green: backend **and** web suites.

## Done criteria

Onglet 1 has a Strict⟷Indicative toggle that defaults to (and re-lands on) strict; indicative
re-renders nappe/smile/greeks under the owner-decided inclusion rule, is **unmistakably badged** as
indicative, and shows per-point provenance so filled-in marks never read as observed; the stored daily
close stays strict and byte-identical; indicative is never persisted and never reaches the risk/strategy
path (tested); the coverage headline reports the active mode's numerator; no look-ahead; all gates green.

## Gotchas

- **The guardrail is the point.** If indicative can be stored, read by a strategy, or confused for the
  close, the task has failed even if it renders beautifully. Strict-canonical is non-negotiable.
- **Indicative is net-new compute, not a flag flip.** There is no permissive path today; budget for the
  engine work and the modelling decision, don't treat it as a frontend-only toggle.
- **Don't guess what indicative prices.** That's the owner/quant call (step 1); the front consumes the
  contract. Specced fixtures can proceed in parallel, the real path waits.
- **Badge can't be missable.** A subtle indicator is how the 15th happened. Indicative must visibly
  reframe the page, not tint one corner.
- **Reuse the design system + taxonomies.** `QcBadge` tones, `lib/format.ts`, and provenance words that
  line up with the quarantine reasons — no new accent, no second vocabulary for the same rows.
