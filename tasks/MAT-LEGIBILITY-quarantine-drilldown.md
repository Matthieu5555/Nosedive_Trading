# MAT-LEGIBILITY-quarantine-drilldown — turn "706 excluded" into "here's why"

> **Owner ask (2026-06-17).** The headline ([MAT-LEGIBILITY-coverage-headline]) tells a PM that ~30%
> of the chain was excluded from the surface. The obvious next question — *why?* — has no answer on
> screen. The reasons already exist (the capture layer classifies every excluded row), but they live
> only in logs. Surfacing them turns an alarming-but-opaque "30% missing" into a legible "here's which
> contracts, and why" — so the operator can tell *thin far wings* (expected) from *a closed market*
> (the canary) at a glance. Legibility means the user can see the cause, not just the symptom.

## What's true today (grounded in code)

- The capture layer **already labels every excluded row**: `cp_rest_close_capture.py`
  `_two_sided_quote_reason` (`:272-284`) returns `missing_side` (bid or ask absent),
  `non_positive_bid` (bid ≤ 0), `non_positive_ask` (ask ≤ 0), or `crossed` (bid > ask). These accumulate
  in `_PromotedSnapshots.drop_reasons` and a `ibkr.close_capture.quarantine_row` log line.
- **But the labels are not persisted to a queryable table** — `raw_market_events` keeps *all* rows
  (two-sided and excluded) faithfully (ADR 0027, `test_raw_faithful_ingestion`), with their bid/ask
  intact, but **without** the reason tag. So the reason is **re-derivable read-only from raw**: for the
  resolved date, re-run the same `bid/ask` classification over the option rows that the fit excluded.
  No new capture, no schema change.
- Nothing in `/api/analytics`, `/api/coverage`, or the web app surfaces excluded-row reasons today.

## Objective

A **drill-in from the coverage headline**: open it and see *why the excluded rows were excluded*,
grouped by reason and (collapsible) by tenor:

> 706 exclues — 512 une face seule · 138 bid ≤ 0 · 41 croisées · 15 ask ≤ 0
> (par ténor: 10d 220 · 1m 140 · … · 3y 96)

so the operator reads the **shape** of the exclusion. A far-wing-heavy, mostly-`missing_side`
distribution is the normal illiquid tail; an *all-tenors, all-rows, all-`non_positive`* distribution is
a closed/degenerate market — the same signature the 2026-06-15 canary would have shown. The point is to
make those two cases look obviously different.

## Design intent

- **A reveal off the headline, not a new page.** The headline ([MAT-LEGIBILITY-coverage-headline]) is
  the always-on read; this is its **disclosure** — the design-skill principle "spend your boldness in
  one place, keep the rest quiet". Closed by default; one affordance ("voir le détail") opens it.
- **Reasons as plain phrases, ranked by count.** `missing_side → "une face seule"`,
  `non_positive_bid → "bid ≤ 0"`, `non_positive_ask → "ask ≤ 0"`, `crossed → "croisées"`. PM register,
  the system's voice, never the raw enum (`analytics-pm-legible-framing`). A single reason→label map
  is the only home for these strings (mirror the kind→label maps already used for signals).
- **Distribution, not a dump.** The value is the *shape* — counts by reason, optionally by tenor —
  not a per-contract list of 706 rows. Reuse the existing table idiom (`CoverageTable`/`BasketLegGrid`)
  for the by-tenor breakdown; keep the by-reason summary to one compact strip.
- **Empty state is direction, not mood** (design-skill): full coverage → "Aucune cotation exclue —
  couverture complète." (an affirmative, not a blank).

## Owns

- **BFF**: extend the analytics `coverage` block (or a sibling `/api/coverage` field — pick the one
  that already resolves the date and reads raw; do not open a third date-resolver) with an
  `excluded_breakdown`: `{ by_reason: {reason, count}[], by_tenor: {tenor, count}[] }`, re-derived from
  `raw_market_events` option rows for the resolved date using the **same** classification predicate as
  capture. Factor the predicate so capture and BFF cannot drift (one source for the bid/ask→reason
  rule). Serializer in `serializers.py`.
- **Front**: a `QuarantineBreakdown` disclosure under the coverage headline + `api.ts` types; the
  reason→label map; tests.

## Depends on / coordinates with

- **Builds on [MAT-LEGIBILITY-coverage-headline]** — the headline is the parent affordance; land the
  headline first, then attach this disclosure. The total (`excluded`) shown here must equal the
  headline's `excluded` (assert it in a test).
- **Reuse the capture predicate, don't reimplement.** `_two_sided_quote_reason`'s logic is the single
  source of the reason taxonomy — the BFF must call/share it (extract to a pure helper if needed) so a
  future reason change updates both. This is the one real refactor in the task.
- **Overlaps [frontend-capture-coverage-panel] phase-2** (quote-completeness on the coverage table):
  both read `raw_market_events` for per-contract quote health. Coordinate so the by-tenor exclusion
  counts and the panel's quote-completeness come from one read, not two divergent passes.
- **Shared-tree:** new component + serializer disjoint; the mount sits under the headline (same
  `Market.tsx` region as the headline) — coordinate that single edit with the front lane.

## What to do (ordered)

1. **Factor the reason predicate.** Extract the bid/ask→reason classification from
   `_two_sided_quote_reason` into a pure, importable helper (capture keeps calling it; behaviour
   byte-identical — pin with the existing capture tests). This is the anti-drift move.
2. **BFF breakdown.** For the resolved date, read `raw_market_events` option rows, classify each
   excluded row with the shared predicate, aggregate `by_reason` and `by_tenor` (reuse
   `surfaces.tenor_years`/`tenor_target_dates` for the tenor map — do not parse `"1m"` again). Sum must
   equal the headline `excluded`. Absent partition → empty breakdown (200), never 500.
3. **Front disclosure.** `QuarantineBreakdown`, closed by default, opens from the headline; reason
   strip + by-tenor table; affirmative empty state when nothing is excluded.
4. **No look-ahead.** Reads only the resolved date's `raw_market_events`; never a later date.

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **Predicate parity — shared-source guard.** A table of hand-built `(bid, ask)` cases maps to the
  expected reason via the extracted helper; the **same** helper is what capture uses (assert capture's
  quarantine count on a fixture equals the BFF's `excluded` total for the same rows). Locks no-drift.
- **Breakdown arithmetic — hand-counted oracle.** A fixture `raw_market_events` with a known mix
  (e.g. 5 `missing_side`, 3 `crossed`, 2 `non_positive_bid` across 2 tenors) → assert `by_reason` and
  `by_tenor` counts, and that `Σ by_reason == Σ by_tenor == headline.excluded`.
- **Front component.** Populated payload renders the ranked reason phrases + by-tenor rows; zero-excluded
  payload renders the affirmative empty state. Assert user-visible text, not internal state.
- **No look-ahead.** Past-date request ignores a later date's raw partitions. `check-lookahead-bias`
  clean on the read path.
- Gate green: backend (`ruff`/`mypy`/`lint-imports`/`pytest`) **and** the web suite.

## Done criteria

A disclosure under the coverage headline shows *why* rows were excluded — counts by plain-language
reason and by tenor — re-derived read-only from `raw_market_events` via the **same** predicate capture
uses (one source, parity-tested); the exclusion total reconciles with the headline; a thin-far-wing day
and a closed-market degenerate close produce **visibly different** distributions; affirmative empty
state on full coverage; no look-ahead; both gates green.

## Gotchas

- **One reason taxonomy.** The extracted predicate is the single source — capture and BFF must not
  carry two copies of the bid/ask→reason rule, or the screen will eventually lie about why.
- **Distribution, not a 706-row dump.** Surface the shape; a per-contract table is out of scope for v1.
- **Reconcile with the headline.** `Σ breakdown == headline.excluded` is a test, not a hope — if they
  disagree the page is internally inconsistent, which is worse than no detail.
- **`raw` is faithful, not pre-filtered.** Excluded rows are in `raw_market_events` with real bid/ask
  (ADR 0027); re-classify them there — don't expect a "quarantined" flag column, it doesn't exist.
- **One tenor map** (`surfaces.tenor_years`); **one date resolver** (reuse `coverage.py`/`health.py`).
