# 1B — Delta-band chain selection: 30Δ put → ATM → 30Δ call per tenor

- **Owns:** the delta-band selection policy in
  `packages/infra/src/algotrading/infra/universe/chain_planning.py` (a variant beside the
  existing %-of-spot `ChainSelection`/`select_strikes`), its config schema under the
  `universe.yaml` strike-selection block, and a new direct unit test for chain selection.
  Conforms to the blueprint (delta definition: `blueprint/09-data-dictionary.md`; the band
  spec: `documentation/roadmap-index-analytics.md` §Phase-1 WS 1B and `vision-medium-term.md`)
  and to **[ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)**
  typed config.
- **Depends on:** P0 — the tenor grid pin (OQ-4: 10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y) and the
  delta-convention pin (OQ-1 follow-up ADR). The band is applied **per tenor**, so the tenor
  set must be fixed first. The pricing engine that yields delta is already built and tested
  (`infra/pricing`: `from_forward`/`from_spot`, `price_european`, `PriceGreeks.delta`).
- **Blocks:** 1C (daily close-snapshot capture selects strikes through this policy) and, via
  the captured chain, the 1F (tenor × delta-band) projection grid.
- **State going in (audited 2026-06-07):** `ChainSelection` is **%-of-spot only**
  (`strike_window_pct` default `0.35`, `min_strikes_per_side` default `10`); `select_strikes()`
  keeps strikes inside `spot ± strike_window_pct` with a per-side floor. There is **no
  delta-band code and no delta-calc in `universe`**. `universe/README.md` states the delta-band
  variant "slots in here as another policy over the same `AvailableChain`, not a parallel
  module". There is **no standalone `test_universe.py`** — `chain_planning` is exercised only
  indirectly through `test_collection_use_cases.py` / `test_orchestration.py` and the per-broker
  discovery suites (README §Test coverage names this a gap to close before 1B lands).

## Objective

A second strike-selection policy over the same `AvailableChain` that, **per tenor**, keeps
every listed strike from the 30Δ put through ATM to the 30Δ call — the whole central smile,
**not** three pillars. The 30Δ bound and the delta convention come from typed config
(`universe.yaml`), never a `.py` literal. Delta is computed from the existing pricing engine,
not re-derived here. The %-of-spot policy stays untouched and selectable; the two coexist as
named policies, matching the README's "one policy, not one per script or per broker" rule.

## What to do (ordered)

1. **Pin the delta convention against the blueprint, in writing, before any math.** The
   pricing engine's `PriceGreeks.delta` is **spot delta** (`dPrice/dspot`,
   `pricing/state.py`), which is exactly the blueprint data-dictionary definition
   (`09-data-dictionary.md`: "first derivative of price with respect to underlying reference
   spot"). The roadmap calls the band the **forward-delta** band (§2, OQ-1 rationale). Reconcile
   them explicitly: build each candidate state with `pricing.from_forward(forward=…, spot=None)`,
   which sets `carry = 0` so **spot delta and forward delta coincide** — at `carry == 0` call
   delta is `discount_factor · N(d1)` and put delta is `-discount_factor · (1 − N(d1))` (see
   `black76.py` lines 88/96, and the existing `test_pricing.py` note "at carry == 0 spot delta
   equals forward delta"). Record in the task PR which of {forward delta with discount, undiscounted
   `N(d1)`} the 30Δ bound is measured in, and pin it as a config field so the choice is auditable —
   do **not** hardcode it. Sign convention: a call's delta is in `[0, 1]`, a put's in `[−1, 0]`,
   so "30Δ put" means delta ≈ `−0.30` and "30Δ call" means delta ≈ `+0.30`; compare on the
   absolute value against the configured bound.

2. **Add the delta-band config.** Extend the `universe.yaml` strike-selection block (per ADR
   0028 typed config — the delta bound is an economic field, so no `.py` default for it) with at
   minimum: the absolute delta bound (`0.30`), the delta convention flag chosen in step 1, and a
   per-tenor minimum strike count so a thin listing still yields a fittable slice. Build the
   policy object through the typed `from_config` path (C7 / ADR 0028), so the YAML↔dataclass
   schema cannot drift and a bad field raises `ConfigFieldError`, never a silent default.

3. **Write the delta-band selection function** beside `select_strikes`, taking the same shape
   inputs (an `AvailableChain`'s listed strikes for one expiry, plus the per-tenor inputs needed
   to price: forward for that tenor, maturity in years, discount factor, and a per-strike or
   single working volatility). For each listed strike, build a call (or put, per the side being
   bounded) state with `from_forward` and read its delta from `price_european`; **keep every
   listed strike whose delta lies between the 30Δ put and the 30Δ call**, i.e. the contiguous
   central block. Selection runs **per tenor** because forward and maturity differ by expiry —
   the same strike is a different delta at a different tenor. Order ascending and de-duplicate,
   exactly as `select_strikes` does.

4. **Thread it as a policy, not a fork.** Let `plan_chain` (and the capture-stage
   `select_capture_keys` ranking) choose between %-of-spot and delta-band by the configured
   policy, reusing `select_chain`/`select_expiries` unchanged. Keep `AvailableChain`,
   `ChainPlan`, and `_BOTH_RIGHTS` as-is — the delta band changes *which strikes*, not the
   contract shapes. Do not introduce a parallel module.

5. **No look-ahead.** Delta must be computed from inputs available **as of the snapshot/date
   being selected for** — the tenor's forward, the working vol, and the discount factor for that
   day, never a later observation. The selection is reference-data-shaped and keys off
   `(instrument_key, as_of_date)` like the rest of `universe`; a historical chain selection for
   day D must price with day-D inputs. Run the `check-lookahead-bias` skill over the new code.

## Test surface

Read [TESTING.md](TESTING.md). Create the direct `chain_planning` unit test the README flags as
the gap to close before 1B. Specific, named cases:

- **`test_delta_band_spans_30d_put_to_30d_call`** — on a hand-built strike ladder at one tenor,
  with a forward, maturity, vol and discount factor chosen so the 30Δ put and 30Δ call strikes
  are known, assert the selected set is exactly the contiguous block of listed strikes between
  them (inclusive). The **expected boundary strikes are derived independently of the selection
  code** — invert `N(d1) = 0.30` by hand (or with `py_vollib`/`scipy`, an oracle independent of
  our engine) to get the boundary log-moneyness, in the test comment; do not call the band
  function to compute its own expected answer.
- **`test_count_varies_with_listing_density`** — the WS 1B acceptance: a dense ladder yields more
  strikes than a sparse one over the *same* delta window; assert the dense count strictly exceeds
  the sparse count and both lie within the band.
- **`test_band_is_per_tenor`** — the same strike is kept at a near tenor and dropped at a far one
  (or vice-versa) because its delta moves with maturity/forward; assert selection differs by tenor.
- **`test_delta_sign_and_atm_included`** — ATM (delta near ±0.50 magnitude) is always inside the
  band; a 10Δ wing (|delta| ≈ 0.10) is excluded; a 30Δ-exactly strike sits **on** the boundary
  and is kept (boundary-exact case).
- **`test_convention_pinned`** — flipping the configured delta-convention flag changes the
  selected boundary as expected; a bad convention value raises `ConfigFieldError`, not a silent
  default (ADR 0028).
- **Edge cases (TESTING.md floor):** empty strike list → `()`; single strike; all-wing ladder
  (nothing inside 30Δ) → the per-tenor minimum-count floor still returns the nearest-the-money
  block, labeled, not an empty silent result; missing/zero forward or non-finite vol → a
  *labeled* failure or the documented fallback, never a bare `NaN` strike. Reference named
  fixtures from the shared library, not inline literals.
- **Regression guard:** the existing %-of-spot `select_strikes` behaviour is unchanged (its
  current indirect coverage in `test_collection_use_cases.py` / `test_orchestration.py` stays
  green).
- Gate green: `uv run ruff && uv run mypy && uv run lint-imports && uv run pytest` (uv only).

## Done criteria

A delta-band selection policy exists beside the %-of-spot one in `chain_planning.py`, selecting
per tenor the contiguous block of listed strikes from the 30Δ put through ATM to the 30Δ call;
the 30Δ bound and delta convention come from typed `universe.yaml` config (no `.py` literal),
reconciled to the blueprint delta definition and built via the engine, not re-derived; the
acceptance holds (count varies with listing density, strikes span the listed 30Δ window); a
direct `chain_planning` unit test with independently-derived expected boundaries exists and is
green; `check-lookahead-bias` passes; root gate green.

## Gotchas

- **Spot delta vs forward delta is the trap.** They are equal **only at `carry == 0`**; using
  `from_forward(spot=None)` enforces that. If a real spot/carry leaks in, the engine returns
  carry-adjusted spot delta (`carry_discount · N(d1)`) and the 30Δ boundary shifts. Pin the
  convention in config and assert it in the test — do not assume.
- **Per tenor, not once.** A single global strike window is the %-of-spot policy's job. The
  delta band must be recomputed per expiry; the same dollar strike is a different delta at each
  maturity. Selecting once on a representative tenor is the silent wrong answer.
- **Whole smile, not three pillars.** The deliverable is *every listed strike* in `[30Δ put,
  30Δ call]`, not {30Δ put, ATM, 30Δ call}. The acceptance ("count varies with listing
  density") only makes sense for the contiguous block.
- **No `.py` literal for `0.30`.** ADR 0028 and C7 already list `ChainSelection (delta band)`
  among the audited hardcode sites to repatriate into validated config; the bound is economic
  and must hash into `universe.yaml`'s `config_hashes`, not sit in code.
- A vol is needed to price for delta, but the surface is built *downstream* of selection — use a
  documented working/seed vol (e.g. an ATM proxy from the raw chain), and state in the test
  comment that the boundary is mildly vol-dependent so the expected strikes are derived at the
  same vol the code uses. Do not pull a fitted surface into the selection step (that is a
  layering inversion and a look-ahead risk).
