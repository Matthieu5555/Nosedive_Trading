# 0004 — Analytics core: the frozen pricing keystone, determinism machinery, coverage floor

- **Status:** accepted
- **Date:** 2026-06-01

## Context

Workstream C (`src/snapshots`, `src/forwards`, `src/iv`, `src/surfaces`,
`src/pricing`) builds the pure-function heart on A's contracts. Each module is a
function from one of A's typed contracts to another, tested against A's fixtures,
with no broker and no dependency on the other workstreams running. Several choices
are not obvious from the code and would otherwise be reverse-engineered — or
re-litigated — by the next agent, especially D (whose risk engine builds against
C's pricing) and E (which drives these functions in replay). They are recorded
here. C owns no contract; the six objects it emits (`MarketStateSnapshot`,
`ForwardCurvePoint`, `IvPoint`, `SurfaceParameters`, `SurfaceGrid`,
`PricingResult`) are all A's, written through A's append-only path.

## Decision

1. **The pricing engine is the frozen keystone, built first.** It is the only
   module allowed to turn a state vector into a price, so the IV solver, Workstream
   D, and E all build against one fixed shape. `PricingState` carries the
   `(forward, spot, carry)` triad with the invariant `forward == spot · exp(carry ·
   maturity_years)` enforced at construction (within a float-round rtol), so the
   forward-form price and the spot-form Greeks can never silently disagree. Two
   constructors express the two ways callers think — `from_forward` (the IV solver,
   the forward engine; carry derived, or zero in the pure-forward/Black-76 view) and
   `from_spot` (a spot and a carry; forward derived). Price and the five Greeks come
   out as the frozen `PriceGreeks`. `PRICER_VERSION` bumps only on a real change to
   the price or Greek formulas, never on config, so it never spuriously moves the
   reproducibility hash. Cash (monetized) Greeks live in the `pricing_result`
   adapter, per unit of underlying, so D scales by contract multiplier × held
   quantity rather than re-deriving them. The shape is pinned by tests
   (`test_pricing.py`: dataclass fields, the public `__all__`, and entry-point
   parameter names) so a C-side change breaks loudly here, not quietly in D.

2. **Determinism is machinery, not hope.** Every C function is pure: no wall-clock
   read, no RNG. `calc_ts` is *injected* at the emission boundary (the contract
   projection), never read inside the math, so each output is a pure function of its
   inputs and a replay reproduces it byte for byte. That claim is backed by real
   tests, not prose (`test_determinism_analytics.py`): a committed golden artifact,
   `tests/golden/analytics_pipeline.json`, produced by running the full chain
   (synthetic surface → forward → IV → surface) and compared on every run;
   regeneration is one deliberate, reviewable command (`C_REGEN_GOLDEN=1 uv run
   pytest -k golden`) whose output lands as a diff in review, never an automatic
   rewrite. Provenance stamp hashes are recomputed in a *separate* Python process
   (no inherited state, `PYTHONHASHSEED` unset) and required to match — this catches
   the classic stamp-from-a-salted-`hash()`/`set`-ordering bug that passes
   in-process and drifts between runs. Source records are canonicalized before
   hashing, so shuffling the input pairs moves neither the forward nor its stamp.

3. **The branch-coverage floor is committed, scoped to the pure core, and kept out
   of the default gate.** `pyproject.toml`'s `[tool.coverage]` pins `branch = true`,
   `fail_under = 90` (the floor; actual is ~98.6%), and `source` = exactly the five
   C dirs. It is run with `uv run pytest --cov`, deliberately *not* folded into the
   `uv run pytest -q` gate: a targeted single-file run during TDD covers only its own
   module, so a whole-core threshold in the default gate would fail even when every
   test passes — training agents to ignore a red exit code. The floor only ever
   rises. B and E are held to behaviour tests, not a coverage number, because their
   bugs live in wiring and timing, not branches. D adds `src/risk` to `source` on its
   own branch; the dir does not exist here, so listing it now would error.

4. **The C→A seam is proven by C, now, through the real pipeline.** Each of the six
   derived contracts is produced by C's *real* code — not hand-built — round-tripped
   through A's `ParquetStore` and asserted equal, so `test_seam_analytics.py` doubles
   as an end-to-end smoke test of the analytics pipeline. A malformed instance of
   each is fed to the store and A's write-ahead validation must reject it with an
   explicit error, not a silent coercion. Tested by the consumer before E's
   integration phase (per `tasks/TESTING.md`), so contract drift surfaces in days as
   a failing C test, not in weeks as an integration mess.

5. **Quote QC is wired into the build path, and its verdict rides beside the
   snapshot — not on it.** Step 7 requires each quote be marked
   `usable`/`caution`/`reject` with reasons and that both the full and filtered
   snapshots be kept. The named checks (`assess_quote`) are not enough on their own:
   they have to be *applied* where snapshots are built, or downstream forward/IV code
   consumes unfiltered quotes. So `build_snapshots` assesses every snapshot it builds
   and returns a `SnapshotBatch` carrying the full set (`snapshots`), the QC-filtered
   subset (`usable`), and the per-snapshot verdicts (`assessed`); `assess_snapshot` is
   the single-instrument equivalent. The verdict lives on the in-memory
   `AssessedSnapshot`, not on A's `MarketStateSnapshot`, because C owns no contract and
   the contract has no QC field — the same rich-result/flat-contract split the forward
   engine already uses (`ForwardEstimate` vs `ForwardCurvePoint`). It is assessed from
   the *raw observed* bid/ask (`None` when absent), not the projected snapshot fields
   (which store `0.0` for a missing side and would read as a spurious locked quote), so
   the verdict is consistent with the `stale_*` flags by construction. Persisting QC as
   queryable rows (`QcResult`) is E's operations-QC plane, fed from this assessed batch.

## Alternatives considered

- **Coverage in the default gate (`addopts`).** Automatic, but every targeted
  single-file run during TDD would then fail the whole-core threshold with all its
  own tests green, and a routinely-red gate is one agents learn to ignore. The
  threshold stays committed in config either way; only the *trigger* is an explicit
  full-suite command.
- **Storing carry redundantly and not enforcing forward consistency.** Simpler
  constructor, but a state whose `forward` and `spot · exp(carry · T)` disagree
  prices the European leg off one anchor and the spot-Greeks off another, silently.
  Enforcing the identity at construction makes every `PricingState` internally
  consistent by definition; the cost is the two constructors.
- **Reading `calc_ts` from the clock inside the pricer.** Convenient, but it makes
  the output a function of wall-clock time and breaks byte-identical replay — the one
  property E depends on. The timestamp is injected at the emission boundary instead.
- **Auto-regenerating the golden on mismatch.** A golden that rewrites itself proves
  nothing; the test would pass through any drift. Regeneration is a deliberate
  env-flagged act and the change is reviewed as a diff.
- **A D→C interface pin living in C.** The *breaking* test belongs in D's suite
  (TESTING.md) so a C-side change fails D loudly, not E. C keeps only a lighter
  shape-pin so its own drift is caught even before D's branch exists; the two are
  complementary, not duplicates.
- **Putting the QC verdict on the snapshot — a contract field or another `flags`
  entry.** A field means editing A's `MarketStateSnapshot`, which C does not own; a
  `flags` string drops the `caution`-vs-`reject` severity and the reason codes,
  collapsing an auditable verdict into one opaque token and conflating QC with the
  market-state flags (open/closed, stale, fallback). Carrying a structured
  `QuoteAssessment` beside the snapshot keeps the contract A's and the verdict whole.

## Consequences

- D builds its risk engine against a frozen, pinned pricing interface. A C-side
  change to the state vector, the Greeks shape, the public surface, or an entry-point
  parameter breaks C's own suite immediately, and D's the moment it lands.
- E can replay a stored day and get byte-identical analytics, because nothing in C
  reads a clock or RNG and every stamp is order-free.
- The coverage number is falsifiable and visible: `uv run pytest --cov` prints the
  per-file branch table and the pass/fail line; raising the floor is a one-line,
  reviewable change to `fail_under`.
- The six derived contracts are known to round-trip and to be rejected when
  malformed *today*, so E's integration inherits a tested seam, not an assumption.
- A regenerated golden shows up as a reviewable JSON diff; an unexplained change to a
  stamp hash or an SVI parameter is visible there, not buried in a green run.

## Addendum — 2026-06-02: `PRICER_VERSION` misnomer corrected (crr → lr)

`PRICER_VERSION` was `black76-crr-1.0.0`. The `crr` tag was wrong: the American engine
has always been Leisen-Reimer (`src/pricing/american.py`), never Cox-Ross-Rubinstein.
The tag is now `black76-lr-1.0.0`.

This is a name correction, not a formula change, so the patch level stays `1.0.0` per
decision 1 above ("bump only on a real change to the price or Greek formulas"). The
cheap test confirms the claim rather than asserting it: with the rename in place,
`test_determinism_analytics`, `test_determinism_risk`, and `test_replay_byte_identical`
all pass against the *committed* goldens unchanged — no committed stamp hash folds in
the version string, so no golden moved and no number moved.

The one visible effect is forward-looking: `PricingResult.pricer_version` on results
produced on or after 2026-06-02 reads `black76-lr-1.0.0`, while results produced before
read `black76-crr-1.0.0`. The two label the identical computation. The discontinuity is
recorded in `documentation/releases/2026-06-02-pricer-version-rename.md` so a future
diff of the label across the boundary is explained, not mysterious.
