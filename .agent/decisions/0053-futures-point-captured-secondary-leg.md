# 0053 — `FuturesPoint`: captured listed-futures term structure as a secondary leg; forward-vs-futures cross-check

- **Status:** Proposed (2026-06-17). Drafted for owner review; not yet ruled.
- **Date:** 2026-06-17.
- **Owner ruling it implements:** the 2026-06-17 **GO** on `tasks/1D-futures-term-structure.md` —
  pursue listed-futures capture **opportunistically**, where IBKR data is obtainable, as a
  secondary cross-check that never displaces the derived forward.
- **Supersedes, for the capture decision only:** [[0037-futures-capture-deferred-forward-only]].
  ADR 0037 deferred futures entirely and noted that a greenlight "would have required" a blueprint
  amendment + a `FuturesPoint`-or-extend decision. This ADR is that greenlight: it lifts the
  *capture* deferral. The information claim of 0037 — futures ≡ option-implied forward, so the
  **derived forward stays primary and sufficient** — is **kept, not reversed**; only "capture
  nothing" becomes "capture as secondary."
- **Relates to:** [[0011-blueprint-as-plan-of-record]] (the blueprint is the amendable domain
  contract; this ADR proposes the amendment, it does not silently rewrite canon),
  [[0042-index-options-only-scope-ibkr-sole-broker]] (scope held — listed *index* futures only,
  IBKR sole provider), [[0017-provider-dimension]] (provider is a first-class partition segment so
  captured futures never mix sources), [[0033-analytical-storage-duckdb-polars-over-parquet]] /
  [[0034-data-retention-compaction-and-backend-disposition]] §4 (storage),
  [[0028-configuration-and-reproducibility-standard]] (tolerance / provider set / roll rule are
  typed config, never `.py` literals), [[0040-ingestion-persistence-invariants]] (raw-before-derived,
  complete-or-flagged). Closes the OQ-4 futures fork left open by 0037.

## Context

The platform's term structure today is the **option-implied forward** `F(T)`, backed out of the
chain by put–call parity (`ForwardCurvePoint`, Eq. 2 / Eq. 4 of `02-math-framework.md`). It is
**derived** and **primary**: Black-76 pricing, IV inversion, forward-delta, log-moneyness, and the
implied carry/dividend split all reference it, and reconstructing it from the chain keeps it
self-consistent with the market's own repo/dividend. ADR 0037 deferred listed futures on exactly
this ground: a listed future and the option-implied forward carry the **same information** about
where the index will settle, so the derived forward needs no external confirmation to be correct.

Two things changed by 2026-06-17:

1. **The teacher's Tab-1 (Données) brief puts a futures term-structure grid in the data tab.** The
   3-onglets transcript (`docs/transcripts/AlgoTradingCourse2-architecture-app-3-onglets.md`, §2–3)
   scopes the first, capture-only tab as "**futures multi-maturités** sur la grille de tenors
   10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y" alongside the ±30Δ option band, and (§5) says the futures
   "serviront ensuite à **couvrir / gérer la position**". So futures are a *named data product* of
   the capture tab and a hedge instrument — not just a redundant forward.
2. **The owner ruled GO**: where listed-futures data is obtainable from IBKR, capture it.

Futures remain **absent from the blueprint** — the founding document (`docs/blueprint/`) never
introduced a futures product, and ADR 0037 kept it that way by design. ADR 0011 makes the blueprint
the amendable contract, so introducing the product is a **blueprint amendment**, and a contract that
lands ahead of that amendment is precisely the drift the discipline exists to prevent. This ADR
therefore (a) records the capture decision and (b) **proposes** the blueprint amendment text below
for owner acceptance — it does not rewrite blueprint canon in place.

## Decision

**Introduce a captured `FuturesPoint` as the secondary, independently-sourced leg of the index term
structure, and a forward-vs-futures cross-check; the derived `ForwardCurvePoint` is never
displaced as primary.**

1. **A new `FuturesPoint` contract, not an extension of `ForwardCurvePoint`.** The captured futures
   leg carries fields the derived forward does not — the **listed contract identifier**, **exchange**,
   **settlement type**, and the **roll/expiry of the listed contract** versus the **pinned tenor** it
   maps onto — and conflating captured-vs-derived on one record would lose the primary/secondary
   distinction that is the whole point. (D1, `tasks/D1-storage-foundation.md`, already reserves the
   name `FuturesPoint` as the gated 1D contract.) It is a frozen, slotted dataclass carrying a
   `ProvenanceStamp`, mirroring the `ForwardCurvePoint` shape:

   ```
   FuturesPoint:
     snapshot_ts:        datetime     # close snapshot in force as-of trade_date
     underlying:         str          # the index symbol (SX5E today)
     maturity_years:     float        # the PINNED tenor this point maps to (grid value)
     expiry_date:        date         # expiry of the LISTED contract backing this tenor
     day_count:          str          # ACT/365, matching the forward
     futures_price:      float        # captured raw, no derivation, no smoothing
     listed_contract_id: str          # the listed-contract identifier (e.g. the front/next conid)
     exchange:           str          # listing venue (Eurex for OESX/FESX family)
     settlement_type:    str          # cash | physical (index futures: cash)
     provider:           str          # IBKR — the capture source, first-class (ADR 0017)
     diagnostics:        FuturesDiagnostics   # tenor-mapping label, roll metadata, quality flag
     source_snapshot_ts: datetime
     provenance:         ProvenanceStamp
   ```

2. **Captured, secondary, raw.** Exactly one provenance-stamped `FuturesPoint` lands per
   `(underlying, pinned tenor)` per close snapshot. **Explicit primary key**
   `(provider, underlying, trade_date, maturity_years)` (declared in the registry, not implied),
   provider-partitioned per D1 (`provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]`), so
   two providers' futures for the same `(underlying, trade_date)` land in **disjoint** partitions
   and a `read` without a `provider` filter never merges them. Captured raw — no derivation, no
   smoothing — and registered **additive-nullable** so a partition written before this lane reads
   back cleanly.

3. **Listed contract → pinned tenor is a config-driven mapping, never invented in code.** Exchanges
   list discrete expiries (quarterly Eurex index futures plus the front months); the analytics grid
   is fixed at `{10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y}`. The **roll/mapping rule** (which listed
   contract backs each pinned tenor, and the roll convention at expiry) is validated typed config
   (ADR 0028) and recorded on each row's diagnostics so it is auditable. A captured point that does
   not map onto a grid tenor is **rejected**; a tenor with no obtainable listed contract surfaces as
   a **coverage gap** (feeds 1H QC), not a silent hole — the derived forward already covers it, so
   absence is **not a defect**.

4. **Forward-vs-futures cross-check — the acceptance bar.** For each `(underlying, tenor)` the
   reconciliation compares the **captured** `FuturesPoint` against the **derived** `ForwardCurvePoint`
   and emits a **labelled triage diagnostic** when they diverge beyond a **configured tolerance**
   (tolerance is typed config, ADR 0028). The derived forward remains **primary** and is **never**
   overwritten, smoothed, or seeded by the futures point. A breach is a **flagged triage record, not
   an exception** (it feeds 1H QC). Because a future and the option-implied forward carry the same
   information, an in-tolerance match is the expected, confirming case; a breach signals bad data or
   a stale/mis-mapped contract on one side.

5. **No look-ahead.** Every cross-check and any carry/roll read uses only the close snapshot in
   force as-of that `trade_date`; today's futures curve is never joined onto a past date. The
   capture + reconciliation path runs clean under `check-lookahead-bias`.

6. **Off the critical path.** 1A→1I ship and pass with forward-only; 1D is a **parallel** cross-check
   and must never become a dependency of any of them. Its absence is not a defect in the main path.

### Proposed blueprint amendment (for owner acceptance — DRAFT, do not treat as merged)

> The text below is **proposed**, per ADR 0011, as the amendment that introduces the futures product
> into the blueprint. It is **not** yet merged into `docs/blueprint/`. On owner acceptance it lands
> as: a new **Equation (F1)** and a paragraph under `02-math-framework.md` ("Forward reconstruction
> and carry"), and three new rows in the `09-data-dictionary.md` table.

**Amendment to `02-math-framework.md` — Forward reconstruction and carry (new paragraph + identity):**

> The system may, where listed-futures data is obtainable, capture the exchange-listed futures
> term structure as a **secondary** estimate of the same forward. A listed future `Φ(T)` and the
> option-implied forward `F(T)` carry the same information about the index's expected settlement, so
> the **parity-reconstructed `F(T)` remains the primary forward** for all pricing, IV, moneyness,
> and carry; the captured future is an **independent confirmation**, reconciled within a documented
> tolerance and never used to displace, smooth, or seed `F(T)`. The captured future is mapped from
> the discrete listed expiry onto the pinned analytics tenor by a documented roll rule.
>
> **Equation F1. Forward–futures consistency (cross-check, not a substitution).**
> $$\left| \Phi(T) - F(T) \right| \le \tau(T)$$
> A breach is a labelled diagnostic (a forward-estimation or data-quality signal), not a correction
> to `F(T)`; `τ(T)` is a configured per-tenor tolerance.

**Amendment to `09-data-dictionary.md` — new rows:**

> | `futures_price` | Captured listed-futures price for a pinned tenor; a **secondary** term-structure source. Never displaces `forward_price`. |
> | `listed_contract_id` | Identifier of the listed futures contract backing a pinned tenor; the listed expiry differs from the pinned tenor and is mapped by a documented roll rule. |
> | `forward_futures_spread` | `futures_price − forward_price` per `(underlying, tenor)`; a reconciliation diagnostic. Beyond a configured tolerance it is a flagged triage record (feeds QC), never an exception. |

## Consequences

- **1D becomes pickable** once this ADR + the blueprint amendment are **accepted** (status moves
  Proposed → accepted on owner ruling). Until then no `FuturesPoint` contract code lands — task 1 of
  the 1D spec is a stop-or-go, not a formality.
- **The contract surface widens additively only.** A new `FuturesPoint` table + registry entry +
  three data-dictionary rows; `ForwardCurvePoint` is untouched, so no existing golden or fixture
  moves on its account. The primary/secondary domain invariant is encoded in the *shape* (two
  separate tables), not left to convention.
- **No data migration.** No futures data exists yet; introducing the table is greenfield.
- **Scope (ADR 0042) holds and is reinforced** — listed **index** futures only, IBKR the sole
  provider; this strictly *adds a secondary confirmation* to the existing index-options path, it does
  not widen the universe or the broker set.
- **Reproducibility:** the new tolerance / provider-set / roll-rule config moves the relevant
  config-hash by design when it lands; `version` is a label, not a reproducibility input (ADR 0028).
