# C8 — Hygiene fixes: gate-green, targeted test gaps, the one determinism leak

- **Owns:** the orthogonal fixes from the 2026-06-05 hygiene audit that are **not** owned by C6
  (collection-seam unification / two-model drift) or C7 (config/hardcoding). Touches
  `apps/frontend/**`, `packages/infra/src/.../actor/valuation_join.py`,
  `packages/infra/src/.../snapshots/quote_quality.py`,
  `packages/infra-deribit/.../collectors/deribit_discovery.py`, plus small error/type cleanups.
- **Depends on:** nothing — all of this is fix-forward on already-landed `packages/`/`apps/` code.
  Independent of C6/C7; can run now and in parallel.
- **Blocks:** nothing, but the gate-red items (below) should land before C5 deletes anything, so a
  red gate is never confused with a deletion regression.
- **State going in:** the analytics/risk core is well-tested and clean; the gaps are concentrated in
  the M4-relocated glue and the frontend. The audit lists exact `file:line`.

## What to do

### A — Gate-green frontend items — **RESOLVED by C4 (2026-06-05), do not redo**
The audit's `apps/frontend` reds (the SPX `test_market_api` assertion, the ~16 mypy errors, and
`runner.py`'s nonexistent `orchestration.build_surface`/`fixtures.library` imports) were **fixed
when C4 landed** (branch `feat/c4-frontend`, root gate green): C4 dropped the Codex `market`/`orders`
routers (which the SPX default lived in), cleared the mypy errors, and left `runner.py`'s SAMPLE
build path as a clean `TODO(C6)` stub. Nothing to do here — kept only as the record of why.

### B — Close the highest-risk test gaps
1. `actor/valuation_join.py` — the C→D join that produces the pricing input has **6
   `ValuationJoinError` modes and unguarded `log`/division math, and zero tests**. Add a seam test:
   a happy 2–3 position fixture asserting the resolved fields, one test per labelled error, plus
   boundary guards (forward=0, maturity=0) raising a labelled `ValuationJoinError` not a bare crash.
2. `snapshots/quote_quality.py` — six boundary predicates (spread, age, OI, intrinsic…) untested.
   Parameterized below/at/above-threshold tests per predicate.

   *(Note: `universe/chain_planning.py` and `universe/service.py` are flagged untested by the audit
   but are part of the M4 dead path — they are **deleted by C6/C5**, not tested here. Do not add
   tests to dead code.)*

### C — The one determinism leak
3. `deribit_discovery.py::discover_instruments` reads wall-clock `datetime.now().date()` to filter
   the universe by maturity window — a **compute input**, so two runs of the same payload on
   different days select different universes and a replay can't reproduce the selection. Inject an
   `as_of`/`now_fn` (mirror the IBKR `cp_rest_adapter` pattern) and add a replay test pinning the
   selected universe to the injected date.

### D — Small error/type cleanups
4. `infra-saxo/.../saxo_transport.py` — four `except Exception` without the justifying comment the
   rest of the saxo code uses (they re-raise correctly; add the one-line `# noqa`/justification).
5. `saxo_underlying.py:97` — `assert self._key is not None` guards *runtime state*; under `python -O`
   it's stripped. Replace with an explicit raise.

## Test surface

Read `tasks/TESTING.md`. The valuation-join seam test and the determinism replay test are the two
load-bearing additions; the rest are boundary/parameterized unit tests. Root gate green after each.

## Done criteria

Root gate green (ruff/mypy/lint-imports/pytest), no `apps/frontend` mypy errors, no failing test;
`valuation_join` and `quote_quality` boundaries covered; Deribit discovery is as-of/deterministic
with a replay test; the noted broad-excepts annotated and the runtime `assert` replaced.

## Gotchas

- Don't test the M4 dead path (`chain_planning`, `universe.service`) — it's slated for deletion
  (C6/C5). Testing it would entrench dead code.
- The `runner.py` orchestration symbols are a C3 dependency — coordinate before deleting vs landing.
