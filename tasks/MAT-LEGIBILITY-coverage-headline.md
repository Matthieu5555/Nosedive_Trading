# MAT-LEGIBILITY-coverage-headline — say how much of the chain the surface rests on, always

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on." Today the
> vol surface renders as a clean, converged nappe with **no hint of how much of the captured chain it
> actually stands on**. The 2026-06-15 canary banked a plausible-looking surface off a closed market;
> on 2026-06-17, 706 of 2,412 option rows (≈30%) were one-sided and silently excluded from the fit —
> and the screen looked identical to a full-coverage day. The surface must **state its own coverage**
> the moment you look at it, the way a chart states its axis. This is the legibility theme's first
> task: the instant honest read.

## What's true today (grounded in code)

- The fit is **strict-only**: `driver.py::_build_iv_points` (`:487`) drops every snapshot that fails
  `_has_two_sided_option_quote` (`bid>0 and ask>0`, ADR 0027) **before** `solve_iv`. So the surface is
  built on a subset of the captured chain, and **nothing on screen says so**.
- The numerator/denominator are **already computed and on disk** — QC's
  `check_underlying_quote_health` (`packages/infra/.../qc/checks.py:139-176`) records
  `two_sided_option_count` and `option_leg_count` in the `qc_results` context. Nothing new to compute.
- `/api/analytics` (`routers/analytics.py`) returns `maturities` + per-slice `surface_slice`
  (`rmse`/`n_points`/`arb_free`/`converged`) but **no coverage block**. `/api/coverage` +
  `CoveragePanel` exist for the per-expiry/per-tenor *strike* table (a detail view, collapsed by
  default) — this headline is the one-glance summary, not that table.

## Objective

A single, always-visible **coverage headline** on Onglet 1 (Données), reading like:

> **Nappe sur 1 706 / 2 412 cotations** · 70,7 % deux-faces · 706 à une face exclues

— sitting with the nappe so a PM sees, before reading any IV, *what fraction of the captured chain the
surface actually rests on*. When coverage is low it is the first thing the eye catches; when coverage
is full it is quiet. No drill-in here (that is [MAT-LEGIBILITY-quarantine-drilldown]) — just the
honest top-line number, every time.

## Design intent (this is a designed element, not a status string)

The locked Onglet-1 reading model (`frontend-page1-reading-model`, `frontend-3onglets-target-ux`) and
the design-system theme are **not** to be reinvented — the bar is to make this read like part of the
page, in the existing type/colour system, answering the page's own question *"qu'est-ce que je
regarde ?"* (`Conseils-front-end:47`).

- **One line, paired numbers.** The signature is the ratio said plainly — *built-on / captured* — not
  a gauge or donut. Numbers in the house sci-notation/units idiom (`lib/format.ts` `sci`/`sciUnit`);
  the fraction as a percent to one decimal. Sits directly under the **② NAPPE 3D** heading so it reads
  as the nappe's caption, not a separate widget.
- **State, not chrome.** Three quiet visual states keyed off the two-sided fraction, reusing the QC
  badge palette already on the page (`QcBadge`) — do not introduce a new accent:
  - **full** (≥ owner-set floor, e.g. ≥95%): muted/neutral — the number is present but recedes.
  - **partial**: warning tone — "706 exclues" carries the eye.
  - **degenerate** (0 two-sided / market-closed close): error tone + plain words "Surface indicative
    — marché probablement fermé", because that is exactly the canary the user must never miss again.
- **Plain words, PM register** (`analytics-pm-legible-framing`): "cotations", "deux-faces", "exclues"
  — never "quarantined rows", "IV points", "snapshots". A label labels.
- **Never silently empty** (`frontend-no-silent-failures`): if the coverage block is missing from the
  payload, the headline says so ("couverture indisponible"), it does not vanish leaving a bare nappe.

## Owns

- **BFF**: add a `coverage` block to the `/api/analytics` response (per surface, i.e. per
  `(underlying, as_of)`): `{ option_rows, two_sided, excluded, two_sided_fraction }`, read from the
  `qc_results` `two_sided_option_count`/`option_leg_count` for the resolved date. Serializer in
  `serializers.py`. Additive-nullable — older payloads without it must still parse.
- **Front**: a small `SurfaceCoverageHeadline` component + its `api.ts` type, mounted under the NAPPE
  heading in `Market.tsx`. Reuses `QcBadge` tones and `lib/format.ts`; no new palette.
- Tests both sides.

## Depends on / coordinates with

- **Reads only already-persisted `qc_results`** — no new capture/compute, no surface recompute.
- **Shares one number with [frontend-capture-coverage-panel] phase-2** (quote-completeness): the
  two-sided fraction must be computed **once** in the BFF and consumed by both the headline (this task)
  and the coverage table's phase-2 add. Do not compute it twice in two endpoints — put it on
  `/api/analytics` `coverage` and have the panel read the same field, or factor a shared helper. Note
  this on the TASKBOARD so the two don't fork the metric.
- **Shared-tree:** the `Market.tsx` mount overlaps the live `frontend-cockpit-ux` lane — claim the row
  and coordinate the one placement edit; the new component + serializer are disjoint.
- Sibling of [MAT-LEGIBILITY-quarantine-drilldown] (the *why*) and
  [MAT-LEGIBILITY-strict-indicative-mode] (the *toggle*). This is the *headline*; ship it first — it is
  the cheapest and unblocks nothing else.

## What to do (ordered)

1. **BFF coverage block.** In the analytics router, for the resolved `(underlying, as_of)`, read the
   `qc_results` underlying-quote-health context → `option_rows = option_leg_count`,
   `two_sided = two_sided_option_count`, `excluded = option_rows - two_sided`,
   `two_sided_fraction = two_sided / option_rows` (guard `option_rows == 0` → fraction `null`, state
   degenerate). Serialize as the additive `coverage` block. Absent `qc_results` → `coverage: null`
   (200), never a 500.
2. **Front type + component.** `SurfaceCoverageHeadline` consumes the block, renders the one-line read
   in the three states above, degrades to "couverture indisponible" on `null`.
3. **Mount under ② NAPPE 3D** in `Market.tsx`, coordinated with the front lane.
4. **No look-ahead.** The block reads only the requested/resolved date's `qc_results`; never a later
   date. Default resolves to the latest date *with data* (mirror `coverage.py`/`health.py`).

## Test surface

Read `tasks/TESTING.md`. Independent oracle; expected values from a source other than the code under test.

- **BFF arithmetic — hand-counted oracle.** A fixture `qc_results` with known
  `option_leg_count`/`two_sided_option_count` (e.g. 2 412 / 1 706) → assert the block returns
  `excluded=706` and `two_sided_fraction≈0.7073`. Zero option rows → `fraction=null`, degenerate flag.
- **Additive/back-compat.** A payload without the block parses on the front (type is nullable);
  absent `qc_results` → `coverage: null`, 200.
- **Front component (component test).** Each of full / partial / degenerate / null payloads renders the
  expected **user-visible text and tone** (assert text + role/state, not internal state). Degenerate
  payload renders the "marché probablement fermé" copy.
- **No look-ahead.** Request for past date D ignores a later date's `qc_results` (inject one; assert
  unchanged). `check-lookahead-bias` clean on the read path.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` **and**
  the web suite (`tsc + lint + vitest + playwright`).

## Done criteria

`/api/analytics` carries an additive `coverage` block sourced from `qc_results`; an always-visible
one-line headline under the 3D nappe states *built-on / captured / excluded* in PM language and the
house number idiom, with quiet/partial/degenerate states off the existing QC palette; a 2026-06-17-style
30%-excluded day and a market-closed degenerate close are **both obvious at a glance**; the two-sided
fraction is computed once and shared with the coverage panel; no look-ahead; both gates green.

## Gotchas

- **Surface only, no recompute.** The numbers exist in `qc_results`; this surfaces them. If a number
  isn't on disk, it isn't in v1.
- **Don't fork the metric.** One two-sided fraction, shared with `frontend-capture-coverage-panel`.
- **Don't reinvent the design system.** Reuse `QcBadge` tones + `lib/format.ts`; the locked reading
  model + design-system theme win. Spend no boldness on a new accent — the boldness is the honesty of
  the number, said plainly.
- **Quiet when full.** Full coverage must not nag — the element recedes; it only raises its voice when
  coverage is partial or degenerate. A banner that always shouts trains the eye to ignore it.
