# P0 — Contracts & unblockers: pin the index-pipeline contracts before any compute

> **Phase 0 of [`documentation/roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md)
> — "Contracts & unblockers (no code until these are pinned)."** Four pins close the load-bearing
> forks (OQ-1/2/3/4) the roadmap left on owner rulings of 2026-06-05: the tenor grid, the $-Greek
> units, the historical-data strategy + the daily-bar contract, and the futures decision. This spec
> writes contract and decision artefacts — a contract field, a data-dictionary row, an ADR, a config
> key. It does **not** build compute; the fetch/capture/projection builds are 1A–1I, cross-referenced
> below and not duplicated here.

- **Owns:** the tenor grid in `documentation/blueprint/09-data-dictionary.md` **and** `configs/`;
  the OQ-1 $-Greek amendment (a new ADR + blueprint note + the `dollar_*` fields on the risk contract
  and the unit strings on the BFF metric contract); the `DailyBar` price-history contract in
  `packages/infra/src/algotrading/infra/contracts/` + its registry entry; the OQ-4 futures ADR
  (accept-and-contract or defer); and moving OQ-1/2/3/4 to *Resolved* in `.agent/open-questions.md`.
  Conforms to the blueprint (ADR 0011, which **overrides this doc on any formula, field, or tenor**)
  and to ADRs 0019/0028/0031/0033/0034.
- **Depends on:** the owner rulings (all four landed 2026-06-05; this turns them into contract). C7
  (the six `configs/` YAML bundles + the typed loader + per-bundle `config_hashes`) is **done** — the
  tenor grid and the OQ-1 flags land as new keys in that existing machinery, not a new loader. D1 (the
  `provider` partition segment) is the storage layout the `DailyBar` table must follow; this spec
  **defines** the contract, D1/1C **persist** it.
- **Blocks:** 1A, 1B, 1C, 1F, 1G, 1H, 1I — no Phase-1 compute starts until these contracts are pinned
  (the roadmap's "no code until these are pinned"). The P0.4 futures decision separately gates 1D.
- **State going in (audited 2026-06-07):** the generic analytics engine is built & tested
  (`infra/{forwards,iv,surfaces,pricing,risk,snapshots,storage}`, `actor/driver.py`,
  `orchestration/{pipeline.py:run_end_of_day,jobs.py,run_state.py,qc_job.py}`, `qc/checks.py` = 10
  checks, collectors live==replay). C7 landed (six `configs/*.yaml` + typed loader + per-bundle
  `config_hashes`). OQ-7/ADR 0029 already renamed the frozen contract fields to the blueprint Part IX
  names — `forward_price`, `implied_vol`, `log_moneyness`, `scenario_pnl`, `qc_status`, `dollar_*` are
  in `contracts/tables.py` today. C4 deleted `store_serving.py` and the `/api/market` router — neither
  exists; the BFF metric contract is reached through the post-C4 readback path
  (`apps/frontend/tests/test_readback_api.py` pins it), **not** `/api/market`. NOT yet pinned: the
  **tenor grid** (no row in the data dictionary, no key in `configs/`), the **$-Greek units/flags**
  (`PricingResult` carries only `dollar_delta/dollar_gamma/dollar_vega` — no `dollar_theta`/
  `dollar_rho`, no unit strings; the data dictionary defines only `dollar_gamma`; gamma-normalisation
  and theta day-count are not config flags), and **futures** (absent from the blueprint entirely).
  There is **no `DailyBar` contract** in `contracts/tables.py` or the registry.

## Objective

Every contract the index pipeline will read is pinned in its canonical home before a line of Phase-1
compute is written: the tenor grid is one ordered list in the blueprint **and** in `configs/`; every
dollar number is defined with an explicit unit and the two genuine convention forks (gamma 1%-vs-$1,
theta 365-vs-252) are config flags, not buried assumptions; the underlying daily-OHLC price history
has a frozen `DailyBar` contract distinct from the option `MarketStateSnapshot`, laid out per ADR 0034
§4; and the futures question is settled in writing — either a contract + ADR, or a recorded deferral
to forward-only. OQ-1/2/3/4 are *Resolved* in the register; the ADRs flagged `(blueprint)` are
accepted. No look-ahead bias is introduced (the daily-bar and membership contracts carry as-of /
effective-dated access); reproducibility is preserved (a new economic config key moves exactly its
bundle's hash). uv only for any command run.

## What to do (ordered)

### Task 1 — Pin the tenor grid (P0.1, OQ-4)

The grid is **10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y** — the prof's spoken grid, which resolves the
vision's `12m`/`1an` duplicate and the out-of-order tail. Pin it in two places, the blueprint being
authoritative:

1. Add a tenor-grid row (or a short list block) to `documentation/blueprint/09-data-dictionary.md`
   (Part IX), giving the eight tenors as one ordered set and the year-fraction each maps to under the
   pipeline's day-count. The data dictionary is the domain contract; per ADR 0011 it overrides config
   if they ever disagree.
2. Add the same ordered grid as a key in `configs/` — per ADR 0028 the standard bundle taxonomy, this
   belongs in `universe.yaml` (it is a selection-grid parameter, the same bundle that already carries
   `ChainSelection`). It is an **economic** parameter, so it enters that bundle's `config_hash`. Do
   not also put it in `environment.yaml` (that bundle never enters the hashes).
3. The two copies must agree by construction. Add a test (Task-5 surface) that asserts the YAML grid
   equals the blueprint grid as an ordered list — drift between them is the failure this guards.

### Task 2 — Pin the $-Greek units + config flags (P0.2, OQ-1) — needs an ADR

OQ-1 is a `(blueprint)` ruling, so this needs a **blueprint amendment + a new ADR** (next free number
is **0035**; 0034 is the current head). Store **raw per-unit Greeks as the source of truth**; the
dollar layer is a derived view with an explicit unit per number:

- **Delta\$** = Δ·S·mult — per \$1 of underlying.
- **Gamma\$** = Γ·S²/100 — per **1% move** (this is the 1%-vs-$1 fork; see flag below).
- **Vega\$** — per **1 vol point** (0.01).
- **Theta\$** — per **calendar day** (÷365; this is the 365-vs-252 fork; see flag below).
- **Rho\$** — per **1% rate**.
- Per-contract (×mult) → per-position (×qty); additive across a book.

1. Write **ADR 0035** (the OQ-1 formalisation): the raw-is-truth / dollar-is-derived split, the five
   unit definitions above verbatim, and the per-contract→per-position→book additivity rule. State it
   amends the blueprint (the dollar conventions live in Part IX / the math notes).
2. Amend `documentation/blueprint/09-data-dictionary.md`: today it defines only `dollar_gamma`. Add
   the sibling rows `dollar_delta`, `dollar_vega`, **`dollar_theta`**, **`dollar_rho`**, each with its
   unit text from the list above, and note on each that the value carries an explicit unit string at
   the BFF boundary.
3. Risk contract: the `PricingResult` in
   `packages/infra/src/algotrading/infra/contracts/tables.py` carries `dollar_delta`, `dollar_gamma`,
   `dollar_vega` only. Add **`dollar_theta`** and **`dollar_rho`** so the dollar layer is complete
   (use the ADR-0029 blueprint `dollar_*` names — they are already the convention here). This is a
   frozen-contract change: additive, with a registry + serialization round-trip and the
   schema-evolution discipline D1 follows (additive-nullable so old partitions still read).
4. BFF metric contract: each dollar number the front receives **carries a unit string** (e.g.
   `"$ per 1% move"` for gamma, `"$ per calendar day"` for theta), not a bare float. This is the
   post-C4 readback contract (pinned by `apps/frontend/tests/test_readback_api.py`); there is no
   `/api/market` router to touch (C4 deleted it). Carry the raw per-unit value **and** the unit string.
5. Make the two genuine convention forks **explicit config flags** in `configs/` (the
   `scenarios.yaml`/`pricing.yaml` bundle that owns the risk-layer params, per ADR 0028): a
   `gamma_normalisation` flag (`one_pct` vs `one_dollar`) and a `theta_day_count` flag (`365` vs
   `252`). Both are economic → they enter that bundle's `config_hash`. The default values match the
   units pinned above (gamma per 1%, theta ÷365).

### Task 3 — Confirm OQ-2 strategy and define the `DailyBar` contract (P0.3)

The historical-data strategy is settled by **[ADR 0031](../.agent/decisions/0031-ibkr-historical-data-cp-rest-oauth1a.md)**:
IBKR is the source; underlying daily OHLC comes via CP REST `/iserver/marketdata/history` (OAuth 1.0a);
the **option** dataset is grown **forward** by the daily close-snapshot capture, with IBKR best-effort
backfill at the start. This task **records** that confirmation and **defines the contract**; the fetch
**implementation** is owned by **[1C-capture-daily-close-and-history.md](1C-capture-daily-close-and-history.md)**
— cross-reference it, do not duplicate the fetch here.

1. In `.agent/open-questions.md`, confirm OQ-2 *Resolved* points at ADR 0031 as the source-of-record
   for the historical strategy (the register currently cites the roadmap §2 ruling; add the ADR link).
2. Define a frozen **`DailyBar`** contract in
   `packages/infra/src/algotrading/infra/contracts/tables.py` (and register it in `registry.py`),
   **distinct from** the option `MarketStateSnapshot`. It is the underlying daily price-history product
   (index + every constituent) that powers the candlestick chart. Carry **full OHLC** (open, high, low,
   close) plus volume, the `trade_date`, the `underlying`, the `provider` (see layout below), a
   `source` / `bar_type` label, and a `ProvenanceStamp` — full OHLC so a candlestick chart is free, per
   the roadmap. Use the blueprint Part IX naming discipline (ADR 0029) for any field that overlaps an
   existing dictionary term. **Make the primary key explicit** — `(provider, underlying, trade_date)`,
   matching the ADR 0034 §4 partition tuple — so row identity is declared, not implied.
3. Storage layout: the `DailyBar` table follows **[ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md) §4**
   — the `provider` partition segment that **[D1-storage-foundation.md](D1-storage-foundation.md)** is
   landing — i.e. `<root>/<layer>/daily_bar/provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]`.
   Store it per **[ADR 0019](../.agent/decisions/0019-one-immutable-raw-model.md)** (one immutable raw
   model) and queried per **[ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)**.
   Mark it in the registry as a **provider-partitioned** table (it is source-specific). This subsumes
   roadmap **WS 1E**: the `ParquetStore` itself is a no-op for this work; the only real 1E deliverable
   is this contract.
4. As-of access: a read of daily bars for a past window must return the bars **as captured for those
   dates** — no forward-fill of a later restatement into an earlier `trade_date`. The `version=<V>`
   segment carries restatement; a default read takes the latest version *per date*, never a later
   date's value. This is the no-look-ahead discipline the membership and config layers already enforce.

### Task 4 — Decide futures capture (P0.4, OQ-4 futures fork) — ADR

Futures are **absent from the blueprint**. The forward path is already built and **primary** — the
put-call-parity-derived forward (`ForwardCurvePoint`) is what Black-76 pricing, IV, forward-delta and
implied-dividend all reference, and backing it out of the chain keeps it self-consistent with the
market's repo/dividend. Listed futures are at most a **secondary** cross-check / hedge instrument.
Make the call in writing — there is no silent third option:

- **Either** greenlight capture: write an ADR (next free number after 0035, i.e. **0036**) **plus a
  blueprint amendment** introducing the futures product, and define the contract — **extend
  `ForwardCurvePoint`** or **add a `FuturesPoint`** in `contracts/tables.py` + registry. The build that
  consumes it is **[1D-futures-term-structure.md](1D-futures-term-structure.md)** (gated on this
  decision) — define the contract here, do not build capture here.
- **Or** defer: record an ADR (0036) that ships **forward-only** for now, stating the forward path is
  built, primary, and sufficient for analytics, and that futures capture is a later increment behind
  this same decision. No blueprint amendment is needed for the deferral.

Whichever is chosen, the OQ-4 futures fork is closed in `.agent/open-questions.md` with the ADR link,
and 1D's gate is set accordingly.

### Task 5 — Close the open questions

Move **OQ-1, OQ-2, OQ-3, OQ-4** to *Resolved* in `.agent/open-questions.md` (OQ-1/3/4 already have
roadmap-§2 rulings recorded; update each to point at the now-accepted ADR, and confirm OQ-2 at ADR
0031). **OQ-3 (point-in-time membership) is pinned here too** — it is a Phase-0 contract pin (the
`(effective_add_date, effective_remove_date)` + as-of-weight shape, gated by `check-lookahead-bias`) —
but its **data build** is **[1A-universe-membership.md](1A-universe-membership.md)**, so close the
ruling and the contract shape here, leave the ingest there. Accept the ADRs flagged `(blueprint)`:
OQ-1 → ADR 0035; the futures decision → ADR 0036.

## Test surface

Read [TESTING.md](TESTING.md). Specific to this spec (these are contract/consistency tests, not math —
the engine math is already covered):

- **Tenor grid consistency (Task 1).** A test asserts the `configs/universe.yaml` tenor grid equals the
  blueprint data-dictionary grid as an **ordered** list of the exact eight tenors (10d, 1m, 3m, 6m,
  12m, 18m, 2y, 3y). Expected list is written literally in the test (independent of the config loader,
  per TESTING's independent-oracle rule), and the order is asserted, not just set membership.
- **Tenor grid hashing (Task 1, Task 2 flags).** Adding/reordering a comment in `universe.yaml` leaves
  its `config_hash` identical; changing a tenor value (or the `gamma_normalisation` / `theta_day_count`
  flag) moves **exactly that bundle's** hash — the C7 reproducibility invariant
  (`packages/infra/tests/` config-hash tests), extended to the new keys. Cross-process hash stability
  per TESTING (compute in a subprocess, no `PYTHONHASHSEED`).
- **`PricingResult` round-trip with the full dollar layer (Task 2).** The C→A contract test
  (`MarketStateSnapshot`, `ForwardCurvePoint`, `IvPoint`, `SurfaceParameters`, `SurfaceGrid`,
  `PricingResult`) is extended: `PricingResult` with `dollar_theta`/`dollar_rho` populated round-trips
  through A's adapter, validates against A's schema, and carries a complete provenance stamp; an old
  partition lacking the two new fields still reads (additive-nullable). Add at least one **malformed**
  `PricingResult` and assert write-ahead validation rejects it explicitly (TESTING's malformed-instance
  rule).
- **Dollar-Greek unit definitions (Task 2).** An independent-oracle test on the dollar layer: for a
  hand-fixture `(Δ, Γ, Vega, Θ, Rho, S, mult, qty)` with the expected dollar numbers computed by hand
  in the test comment (Delta\$=Δ·S·mult, Gamma\$=Γ·S²/100, Vega\$ per 0.01, Theta\$ ÷365, Rho\$ per 1%),
  the computed dollar Greeks equal the hand values within float tolerance, and per-position = per-
  contract × qty (additivity over a 2–3 leg book equals the hand sum — TESTING's risk-aggregation rule).
- **BFF metric carries a unit string (Task 2).** Extend `apps/frontend/tests/test_readback_api.py`
  (the post-C4 readback seam — there is no `/api/market` to test): every dollar metric the front reads
  back carries a non-empty unit string matching the pinned convention (e.g. gamma → "per 1% move",
  theta → "per calendar day"), and the raw per-unit value is present beside it. This is the BFF↔infra
  drift guard.
- **Config-flag effect (Task 2).** `gamma_normalisation = one_dollar` vs `one_pct`, and
  `theta_day_count = 252` vs `365`, each produce the correspondingly different dollar number (assert the
  exact ratio: 1%→$1 is ×100 on gamma, 365→252 changes theta by the day-count ratio). The flag must
  actually change the output, not be inert — feed both and assert they differ as expected.
- **`DailyBar` contract round-trip (Task 3).** A `DailyBar` with full OHLC + volume + `provider` +
  stamp writes and reads back **equal** through A's adapter (the B→A `RawMarketEvent` seam pattern),
  validates against A's schema, and lands under
  `daily_bar/provider=<P>/trade_date=<D>/underlying=<SYM>` (ADR 0034 §4). Two providers writing the
  same `(underlying, trade_date)` land in **disjoint** partitions (the D1 invariant). One malformed
  `DailyBar` (e.g. high < low) is rejected with an explicit error, not coerced.
- **`DailyBar` ≠ `MarketStateSnapshot` (Task 3).** A test asserts the two contracts are distinct
  registry entries with distinct tables — a `DailyBar` does not validate as a snapshot and vice versa
  (guards the "distinct product" requirement).
- **As-of / no-look-ahead read (Task 3).** A read of daily bars for date D returns the bar captured
  **for D**, not a later restatement forward-filled onto D; with two `version` segments for the same
  `(provider, trade_date, underlying)`, the default read takes the latest version **for that date**.
  Gate the historical-join path with `check-lookahead-bias`.
- **Open-questions / ADR acceptance (Tasks 4–5).** A doc-consistency check (or the existing register
  guard) that OQ-1/2/3/4 are in *Resolved* with ADR links, and that ADR 0035 (and 0036 if futures are
  greenlit) exist and are marked accepted.

## Done criteria

The tenor grid is one ordered set of the eight tenors in `documentation/blueprint/09-data-dictionary.md`
**and** in `configs/universe.yaml`, consistent by test, and the YAML grid enters its bundle's
`config_hash`. ADR 0035 is accepted and the data dictionary defines `dollar_delta`/`dollar_vega`/
`dollar_theta`/`dollar_rho` beside `dollar_gamma` with their unit text; `PricingResult` carries
`dollar_theta` + `dollar_rho` (additive-nullable, round-trips, stamped); the BFF metric contract emits
each dollar number with an explicit unit string beside its raw per-unit value; `gamma_normalisation`
and `theta_day_count` are economic config flags that move their bundle hash. The `DailyBar` full-OHLC
contract exists in `contracts/tables.py` + `registry.py`, distinct from `MarketStateSnapshot`,
provider-partitioned per ADR 0034 §4, stored per ADR 0019 / 0033, with as-of reads that admit no
look-ahead — subsuming WS 1E. The futures decision is recorded as ADR 0036 (capture + blueprint
amendment + contract, **or** documented forward-only deferral), with 1D's gate set accordingly.
OQ-1/2/3/4 are *Resolved* in `.agent/open-questions.md` with ADR links; OQ-3's contract shape is pinned
here (build deferred to 1A). The root gate is green (`uv run ruff && uv run mypy && lint-imports &&
uv run pytest`).

## Gotchas

- **The blueprint overrides.** Per ADR 0011 the data dictionary is the domain contract; if the
  `configs/` grid and the blueprint grid ever diverge, the blueprint wins and the test that pins their
  equality is the alarm. Pin the blueprint row first, then mirror it into YAML — never the reverse.
- **Don't re-rule what's already ruled.** OQ-1/2/3/4 were owner-ruled 2026-06-05; this spec turns the
  rulings into contract, it does not re-open them. State the options for futures (Task 4) neutrally only
  to the extent the ADR records the decision — the owner has expressed forward-is-primary.
- **Field names are already blueprint-conformant (ADR 0029 / OQ-7, 2026-06-06).** Use `forward_price`,
  `implied_vol`, `log_moneyness`, `scenario_pnl`, `qc_status`, `dollar_*` — do **not** reintroduce the
  old `forward`/`iv`/`k`/`pnl`/`status`/`cash_*` names. The new `dollar_theta`/`dollar_rho` follow the
  same `dollar_*` pattern.
- **C4 deleted `store_serving.py` and the `/api/market` router.** Do not wire the BFF unit strings
  through either — they are gone. The metric contract is the post-C4 readback path pinned by
  `apps/frontend/tests/test_readback_api.py`.
- **`DailyBar` is not the option snapshot.** It is a separate product (underlying daily OHLC for the
  charts) with full OHLC for free candlesticks — keep it a distinct contract and a distinct table, not
  a field on `MarketStateSnapshot`. WS 1E is *only* this contract; the `ParquetStore` work is a no-op.
- **The fetch is not this task.** P0.3 defines the contract and confirms the strategy; the IBKR
  `/iserver/marketdata/history` fetch and the daily close-snapshot capture mode are 1C. Cross-reference,
  do not duplicate — duplicating the fetch spec here would create two sources of truth for one build.
- **No new economic literal in `.py`.** Per C7 / ADR 0028 the tenor grid and the two convention flags
  live in `configs/`, hashed; do not hardcode them in compute. `environment.yaml` never enters the
  hashes, so the grid and flags belong in `universe`/`pricing`/`scenarios`, not there.
- **No look-ahead in the daily-bar read.** A restated bar (`version=<V>`) must never leak backward onto
  an earlier `trade_date`; the default read is latest-version-per-date. Run `check-lookahead-bias` over
  any historical-join code 1A/1C add against this contract.
- **uv only** for every command (`uv run pytest`, etc.); do not invoke a bare `python`/`pip`.
- **ADR numbering:** 0034 is the current head, so OQ-1 → 0035 and the futures decision → 0036. Confirm
  no one else has claimed those numbers before writing (append-only directory).
