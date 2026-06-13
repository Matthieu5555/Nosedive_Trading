# T-tenor-selection — capture the pinned tenor grid, not the nearest N expiries

> **PRIORITY (owner, 2026-06-12).** The EOD capture silently captures **only the front
> couple of weeks** of expiries and never builds a term structure. The professor explicitly
> asked for the 2y/3y points; they are **not** in our data. Root cause is below. This is the
> upstream fix that **F-SURF-01 and F-IBKR-02 are downstream symptoms of** — do not close
> those two green in isolation (see the audit addendum). Claim the files on the TASKBOARD
> before editing; another agent runs the same tree.

## The bug (verified 2026-06-11 capture + live secdef 2026-06-12)

The capture honours the **length** of `tenor_grid` but never its **values**. Two-stage truncation:

1. `_selection_from_config` (`packages/infra-ibkr/src/algotrading/infra_ibkr/collectors/cp_rest_close_capture.py:634`)
   sets `max_expiries = len(config.universe.tenor_grid)` = **8** — the labels `10d,1m,…,3y` are
   thrown away, only the count survives.
2. `_discover_chain` (same file, `:356`) qualifies `months[: max_expiries]` (the first 8 **month
   tokens**), then `plan_chain` → `select_expiries(chosen.expirations, max_expiries)`
   (`packages/infra/src/algotrading/infra/universe/chain_planning.py:218`) keeps the **8 nearest
   expiries chronologically**. Because the front month alone lists ~8 weeklies, *every* later
   expiry is discarded. On 2026-06-11 the kept set was **8 June expiries (06-10 … 06-22)** for both
   SPX and SX5E.

Downstream consequence: the fitted surface spans ~2 weeks; projection onto `1m…3y`
(`projection.py:481`, `tenor_years(label)`) lands outside the fitted domain → **zero cells**; QC
`check_tenor_coverage_floor` (`qc/checks.py:589`) fired **8 breaches** (10d=2, 1m…3y=0). F-SURF-01
(`projection.py` DF→1.0, *"pinned-tenor keys never match listed-expiry keys"*) is the same key
mismatch seen from the projection side.

**The broker is NOT the limit.** Measured live via the CP Gateway (`IBKR_CP_GATEWAY=1`,
authenticated 2026-06-12):

| Index | `secdef/search` OPT month tokens | Furthest listed |
|-------|----------------------------------|-----------------|
| SPX (conid 416904, symbol `SPX`)     | 19 | **DEC 2031** (~5y) |
| SX5E (conid 4356500, symbol `ESTX50`) | 28 | **DEC 2035** (~9y) |

A month token deflates to several expiries through `secdef/info` (e.g. `JUN26` → 5 weeklies,
`DEC28` → 1 quarterly). So 2y/3y are fully available; the gap is 100% our selection.

## Objective

Capture, per pinned tenor `{10d,1m,3m,6m,12m,18m,2y,3y}`, the listed expiry(ies) that **straddle**
each tenor's target date, so the fitted surface actually spans the grid and the projection
interpolates rather than extrapolates. Replace "nearest N expiries" with "nearest-to-each-tenor".

## Owns

- A tenor→expiry **resolver** in `chain_planning.py` (new `select_expiries_for_tenors(...)` or
  equivalent), alongside the existing `select_expiries` (keep it; it has other callers / tests).
- The wiring in `_selection_from_config` and `_discover_chain`
  (`cp_rest_close_capture.py`) so the **tenor labels** (not just the count) reach the resolver, and
  discovery qualifies the **month tokens that contain the resolved expiries** — not `months[:N]`.
- The canonical selection artifact: a **dict `tenor_label → expiry` (or `→ {below, above}` for the
  bracket)**. The broker request list is the **derived** `sorted(set(values))`. The tenor label is
  bound to the expiry **here**, at selection — that binding is what T-tenor-key-contract carries
  end-to-end to kill the F-SURF-01 mismatch at source.

## What to do (ordered)

1. **Selection policy = bracket.** For each tenor label, target = `trade_date + tenor_years(label)`
   (`tenor_years` in `projection.py:185` is the **single home** of the label→year-fraction map —
   import it, do **not** re-parse `"10d"` anywhere). From the listed expiries, pick the nearest
   expiry **at or below** and the nearest **at or above** the target. Dedup across tenors
   (adjacent short tenors collide — fine). The owner ruled selection mode after measuring the live
   chain; bracket is the recorded default. If a single nearest is later preferred, it is a
   one-line policy switch, not a rewrite.
2. **Residual-gap handling.** If no listed expiry exists on one side of a target (the long end:
   SPX has `…JUN27, SEP27, DEC27, DEC28, DEC29…`, so a 2y target ≈ Jun-2028 has `DEC27` below and
   `DEC28` above — fine; but `3y`/`5y`-style edges can run out), record the tenor's coverage as a
   **labeled one-sided / absent** entry in the artifact — never a silent drop. T-tenor-key-contract
   defines how QC reads it; here, just emit the label.
3. **Discovery targets the right months.** Rewrite the `months[: selection.max_expiries]` loop
   (`_discover_chain`) to qualify the month tokens that **contain** the resolved bracket expiries.
   Map each target date → its month token (e.g. `2028-12-14` → `DEC28`), qualify those tokens'
   strikes, and keep only the resolved expiries from each token's `secdef/info` deflation. Do not
   re-introduce a front-loaded `[:N]` slice.
4. **Keep the strike policy unchanged.** `_DISCOVERY_STRIKES_PER_SIDE` (±16) and the downstream 30Δ
   delta band are orthogonal and stay as-is. Only the *expiry* axis changes.
5. **No look-ahead.** Target dates derive only from `trade_date` and the static grid; expiry
   selection reads only the chain listed as-of that `trade_date`. Selection must be **deterministic**
   so capture stays byte-identical on replay (ADR 0027). Run `check-lookahead-bias` over the path.

## Test surface

Read `tasks/TESTING.md`. Independent oracles mandatory; expected values from a source other than the
code under test.

- **Bracket correctness — independent oracle.** Hand-build a synthetic listed-expiry set spanning
  10d…4y (in the test comment, dates computed by hand from a fixed `trade_date`). For each pinned
  tenor assert the two selected expiries are exactly the listed dates immediately below and above
  `trade_date + tenor_years(label)`. Test a target that falls **exactly on** a listed expiry
  (boundary): that expiry is selected, no spurious second pick.
- **Collision dedup.** Short tenors whose brackets overlap (10d, 1m in a dense front month) produce
  a **deduplicated** broker request list — assert no expiry appears twice and the count is the
  union, not the sum.
- **Long-end gap.** A grid whose furthest listed expiry is short of `3y` surfaces `3y` as a
  **labeled one-sided/absent** entry, not a dropped tenor and not a crash.
- **Month-token mapping.** A resolved expiry `2028-12-14` maps to month token `DEC28`; discovery
  qualifies `DEC28` and keeps only that expiry from its `secdef/info` deflation (assert non-target
  expiries in the same token are not captured).
- **No front-load regression.** With a chain identical to 2026-06-11's, the selection must **not**
  collapse to 8 June expiries — assert it spans ≥ 6 distinct months reaching ≥ 18 months out.
- **No look-ahead / determinism.** Re-running selection on the same as-of chain yields an identical
  ordered artifact; injecting a later-dated expiry list for a *future* date does not change the
  past date's selection. `check-lookahead-bias` clean.
- **Edge cases (the floor).** Empty chain, single listed expiry, all expiries past the longest
  tenor, NaN/garbage maturityDate — each a labeled outcome, never a crash or silent pass.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.

## Done criteria

The capture selects expiries by **tenor target (bracket)**, not nearest-N; the kept set spans the
pinned grid out to the longest tenor the broker lists (SPX→2031, SX5E→2035 confirm 2y/3y are
reachable); the selection artifact is a tenor-keyed dict with the broker list derived from it;
residual one-sided/absent tenors are **labeled**; discovery qualifies the containing month tokens;
selection is deterministic (replay-stable) and look-ahead-clean; root gate green. The QC
`tenor_coverage_floor` breaches from 2026-06-11 are gone for every tenor the broker actually lists.

## Gotchas

- **Two truncations, not one.** Fixing only `_discover_chain`'s `months[:N]` is not enough — the
  `plan_chain`→`select_expiries(...,max=N)` gate **re-collapses** to the nearest N. Both must move
  to the tenor-targeted resolver, or the bug survives behind a green-looking patch (this is exactly
  why **F-IBKR-02**'s "sort chronologically before slicing" does **not** fix it — sorted nearest-8
  is still nearest-8).
- **One home for the tenor map.** `tenor_years` (`projection.py:185`) is the only label→years map;
  importing it keeps capture and projection on the **same** year fractions (the join key
  T-tenor-key-contract relies on). Re-parsing `"10d"` in the collector re-opens the F-SURF-01
  mismatch.
- **Month token ≠ expiry.** `secdef/info` for one token returns *all* that token's expiries; select
  the resolved ones, don't capture the whole token blindly (that re-floods the front month).
- **Pacing budget.** Targeting ~8 spread tenors qualifies strikes across more month tokens than the
  old front-loaded slice. Far tokens are thin (DEC29 listed ≈ 91 strikes; we take ±16/side), so the
  added `secdef/info` paced sweeps are modest — but confirm against the broker.yaml pacing bands
  (C7 / ADR 0028); do not hardcode a sleep.
- **Replay is a domain invariant.** Selection must be a pure function of `(as-of chain, trade_date,
  grid)` — no wall-clock, no set-iteration nondeterminism — or the byte-identical re-capture
  guarantee (ADR 0027) breaks.
- **uv only** for any environment/dependency work.
