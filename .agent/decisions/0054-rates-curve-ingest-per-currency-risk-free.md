# 0054 — Per-currency risk-free rate-curve `r(T)` ingest; Rho against the external curve; implied−riskfree QC

- **Status:** Proposed (2026-06-17). Drafted for owner review; not yet ruled.
- **Date:** 2026-06-17.
- **Implements:** TARGET §4 ruling **R1** + §7.5 + §5.3, via `tasks/infra-rates-curve-ingest.md`
  (R1: "needs an ADR + blueprint amendment before build" — R1 changes a domain contract, ADR 0011).
- **Builds on (landed):** [[0028-configuration-and-reproducibility-standard]] and the
  `T-explicit-rate-parameter` step-1 land — `ForwardConfig.rate: float | None` is the explicit
  interest-rate **input** the blueprint pins (`platform_config.py:362`; `pricing.yaml` ships
  `rate: null`; consumed by `forwards/estimate.py._carry_and_dividend`). This ADR is the **next**
  step in that sequence: a flat `rate` scalar becomes an ingested per-currency curve `r(T)`.
- **Relates to:** [[0011-blueprint-as-plan-of-record]] (the blueprint is the amendable contract;
  this ADR proposes the amendment, not a silent rewrite),
  [[0042-index-options-only-scope-ibkr-sole-broker]] (scope held — index-options-only, IBKR sole
  broker; this adds an *ingested reference curve*, not a new tradable or broker),
  [[0019-one-immutable-raw-model]] / [[0033-analytical-storage-duckdb-polars-over-parquet]] /
  [[0034-data-retention-compaction-and-backend-disposition]] (the `rates` table is a daily as-of
  raw input, stored and retained like any other), [[0036-dollar-greek-units-and-monetization-conventions]]
  (Rho's `dollar_rho` unit convention is unchanged — only its *basis* moves to the external curve).
  Sequence (audit §D.1): `T-explicit-rate-parameter` (param, landed) → `T-scenario-rate-axis`
  (shock) → **this** (real curve).

## Context

The only rate in the system is **back-derived**: the per-expiry parity-implied rate, `r = −ln(DF)/T`
off the put–call-parity forward (`pricing/black76.py`), with Rho computed against it. The landed
`ForwardConfig.rate` lets an operator **override** that with one flat scalar, but neither the implied
rate nor a flat override is an **external, term-structured** risk-free curve.

This matters for two blueprint-grounded reasons:

1. **Eq. 5 needs `r(T)` as an *input*.** `q(T) = r(T) − (1/T)·ln(F(T)/S₀)` (`02-math-framework.md`):
   `r` is the input, the carry/dividend `q` is **derived**. You cannot split the parity forward's
   carry into a *rate* component and a *dividend* component without an independent `r(T)`. A flat
   scalar is a degenerate (constant) curve; the blueprint's Step 6(f) explicitly contemplates a
   **rate curve** ("If a rate curve is available, derive implied carry/dividend yield and compare it
   with expectations").

2. **A book-level "rates +50bp" answer is meaningless against a per-expiry *implied* rate.** Rho as
   a desk-reportable risk needs a single, bumpable, externally-anchored curve per currency, not a
   rate that is itself a function of the chain it is supposed to risk.

R1 changes a domain contract (a new ingested `rates` table + Rho's basis + a new spread diagnostic),
so per ADR 0011 it requires a blueprint amendment. This ADR records the decision and **proposes** the
amendment text below for owner acceptance; it does not rewrite blueprint canon in place.

## Decision

**Ingest a per-currency risk-free curve `r(T)` as a daily as-of table; make it the *risk* rate that
Rho is bumped against, while the parity-implied rate stays the *pricing-consistency* rate; persist
and QC the implied−riskfree spread.**

1. **`rates` table — a daily as-of curve, per currency.** Contract
   `rates(currency, pillar_tenor, rate, as_of)`: **Euribor/€STR pillars for EUR** (SX5E today),
   **SOFR for USD** (SPX, when unparked). Config names the **source per currency** (typed config,
   ADR 0028 — never a `.py` literal). It is an **as-of** table: a reconstruction for past day D reads
   only the curve published as-of D — no look-ahead, no joining today's curve onto a past valuation
   (the as-of abstraction of AGENTS.md's no-look-ahead rule). Stored and retained as a raw daily
   input (ADR 0019/0034); registered **additive-nullable** so partitions written before this lane
   read back cleanly.

2. **`r(T)` from the curve, not a scalar.** Pricing/forward consumption evolves from the landed flat
   `ForwardConfig.rate` to evaluating the ingested curve at the option's `maturity_years` (pillar
   interpolation — linear in zero rate or in a documented convention named in config). The landed
   flat `rate` remains the **fallback / override** (and `rate: null` keeps the byte-identical
   parity-implied behaviour). `ForwardConfig.rate` stays the typed-config *home* of the rate input;
   the curve is its term-structured generalisation, **coherent with Eq. 5** — `r` is the input, `q`
   is derived.

3. **Two rates, two distinct roles — kept separate, not merged.**
   - The **parity-implied rate** (`−ln(DF)/T`) stays the **pricing-consistency** rate: it is what
     keeps Black-76 pricing self-consistent with the chain the forward was reconstructed from. It is
     **not** displaced. The PCP forward stays observable and primary (this preserves ADR 0037 / ADR
     0053's "derived forward is primary").
   - The **ingested external curve** `r(T)` is the **risk** rate: Rho becomes the sensitivity to
     *this* curve, bumped per currency. A "rates +50bp" book answer is now well-defined — it bumps
     the external curve, not the chain-implied rate. (Pairs with the second-order-greeks lane;
     the `T-scenario-rate-axis` shock plugs in here as a base curve + a shock axis.)

4. **implied − risk-free spread = first-class diagnostic + QC gate.** Per `(currency, tenor)`,
   persist `implied_rate − r(T)` as a labelled diagnostic — a funding / dividend / borrow signal —
   and add a QC check that flags it beyond a configured bound (a forward-estimation sanity gate:
   a large persistent spread is a real funding/borrow signal *or* a bad parity forward to
   quarantine). Derived from the persisted rows; not a guess, never a bare NaN.

5. **Index-options-only / IBKR-sole-broker scope (ADR 0042) holds.** This adds an **ingested
   reference curve**, not a new tradable instrument and not a new broker. The curve source is a
   per-currency config knob (its provider is whatever the config names — it does not widen the
   trading universe or the live-broker set).

### Proposed blueprint amendment (for owner acceptance — DRAFT, do not treat as merged)

> Proposed per ADR 0011 as the amendment introducing the external rate curve. **Not** yet merged
> into `docs/blueprint/`. On owner acceptance it lands as a clarifying paragraph under
> `02-math-framework.md` ("Forward reconstruction and carry", which already contemplates "the system
> may maintain a rate curve and derive an implied carry or dividend yield") and two new
> `09-data-dictionary.md` rows.

**Amendment to `02-math-framework.md` — Forward reconstruction and carry (clarifying paragraph):**

> The rate curve `r(T)` of Eq. 5 is an **ingested, per-currency external** input (Euribor/€STR for
> EUR, SOFR for USD), captured daily as an as-of table — distinct from the per-expiry
> **parity-implied** rate `−ln(DF)/T` backed out of the chain. The two play different roles and are
> kept separate: the **parity-implied** rate is the **pricing-consistency** rate (it keeps Black-76
> self-consistent with the chain and is never displaced); the **ingested** `r(T)` is the **risk**
> rate, the basis Rho is bumped against and the `r` that splits the parity forward's carry into rate
> and dividend (Eq. 5, `q` derived). Where no external curve is available, `r(T)` degenerates to a
> documented flat-rate fallback.

**Amendment to `09-data-dictionary.md` — new rows:**

> | `risk_free_rate` | Ingested per-currency risk-free rate at a pillar tenor (`rates` table), as-of dated. The Eq. 5 `r(T)` **input** and the **risk** rate Rho is bumped against — distinct from the parity-implied pricing-consistency rate. |
> | `implied_riskfree_spread` | `implied_rate − risk_free_rate` per `(currency, tenor)`; a funding/dividend/borrow diagnostic and a QC gate on forward estimation. Beyond a configured bound it is a flagged triage record, never an exception. |

## Consequences

- **R1 (`infra-rates-curve-ingest`) becomes pickable** once this ADR + the blueprint amendment are
  **accepted** (Proposed → accepted on owner ruling). Until then it stays parked — no `rates`-table
  code lands ahead of the amendment.
- **Two rates by design.** The parity-implied (pricing-consistency) rate and the ingested (risk)
  rate coexist deliberately; a reader/agent must not "simplify" them into one. The separation is the
  decision, not an oversight.
- **Goldens / config hash.** Adding the curve source + interpolation convention + spread bound to
  typed config moves the `pricing` config-hash by design; Rho's *value* moves once it is bumped
  against the external curve instead of the implied rate — goldens regenerated by design when built.
- **Additive, no migration.** The `rates` table is a new daily as-of input; existing partitions and
  contracts are untouched. `dollar_rho`'s unit convention (ADR 0036) is unchanged — only its basis
  moves.
- **No code, config, or schema change is made by this ADR** — it records the decision and proposes
  the amendment only.
