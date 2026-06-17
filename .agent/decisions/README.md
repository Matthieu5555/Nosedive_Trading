# Decisions — one-line index

Read **this index first**, not all 31 ADR bodies. Open an ADR only when you need the *why* behind a
rule it names. Domain questions (formulas, contracts, field names, vocabulary) are **not** here —
they live in **`TARGET.md`** (repo root, the domain + strategy authority). ADRs record
**process/architecture/operational** choices that TARGET deliberately leaves to the build.

> **Scope today (ADR 0042):** index-options-only, **IBKR the sole live broker**, **SX5E** live, SPX
> parked, single names are index *constituents* (and dispersion-phase underlyings — registry-driven,
> never hand-set). Removed/superseded ADRs are not kept in-tree — **git history is the archive**.

## Scope & brokers
- **0042** — Index-options-only; IBKR sole broker; SX5E first, SPX parked. *(the scope record)*
- **0043** — A booked fill is a **concrete contract**, resolved at booking time (grid-cell ticket → `(strike, expiry, right)` + paper mark). *(execution booking chain)*
- **0012** — One `infra-<broker>` leaf package per broker (today only `infra-ibkr`).
- **0017** — `provider` is a first-class field + partition key (generic; one live provider, IBKR).
- **0024** — IBKR over the Client-Portal REST API (course requirement), under the Nautilus spine.
- **0031** — IBKR historical daily bars over CP REST, OAuth 1.0a (pycryptodome).
- **0035** — Index registry in `universe.yaml`; per-index capture scheduled off the exchange calendar.
- **0037** — Futures deferred: ship forward-only (gates task 1D).
- **0053** *(Proposed)* — `FuturesPoint`: captured listed-futures term structure as a **secondary** leg + a forward-vs-futures cross-check; derived forward stays primary. Supersedes 0037's *capture* deferral only. Unblocks task 1D.

## Architecture & runtime
- **0011** — Plan of record governs the domain, AGENTS.md governs process. (The founding blueprint was retired as out-of-date; `TARGET.md` is now that authority.)
- **0001** — Workspace layout + the `.agent/` instruction layer.
- **0023** — Nautilus is the runtime spine; lean on proven libraries; retire the hand-rolled session.
- **0025** — Analytics hosted in Nautilus; our raw layer stays the system of record (no-dual-path).
- **0026** — One actor-driven orchestration/observability layer.
- **0027** — Collection seam: the push `RawCollector` is canonical; the pull seam is retired.
- **0032** — Unattended scheduling via systemd timers, not an orchestration platform.

> The layered uv-workspace + import-linter layering is described in `.agent/map.md` (Monorepo row)
> + `pyproject.toml`; the `StorageRepository` seam is ADR 0015.

## Data, storage & ingestion
- **0019** — One immutable, flat-EAV `RawMarketEvent` over Parquet (append-only raw layer).
- **0015** — `StorageRepository` port + tiered backends (DuckDB query, SQLite metadata).
- **0033** — Analytical storage/query: DuckDB + Polars over the immutable Parquet store.
- **0034** — Retention tiers, cold-compaction, backend disposition (no Postgres in core).
- **0039** — Raw-schema bridge: broker-raw ↔ contracts seam; reproducible samples.
- **0040** — Ingestion invariants: raw-before-derived, complete-or-flagged, one persist orchestrator.
- **0041** — EOD re-fire **overwrites** rather than skips (idempotency model).
- **0028** — Config & reproducibility: YAML → typed config → per-bundle hashes; as-of profiles. No `.py` literals.

## Analytics, risk & QC
- **0002** — Foundation hardening: lineage keys, atomic writes, schema enforcement.
- **0003** — Market-data plane: content-addressed idempotency, gap encoding, broker seam.
- **0004** — Frozen pricing keystone: determinism machinery, `PRICER_VERSION`, coverage floor. *(also holds the former 0005 test-surface conventions)*
- **0006** — Risk engine: valuation seam, net/monetization conventions, scenario grid.
- **0009** — Surface job + broker-agnostic chain planning + market-data policy.
- **0010** — QC + validation merged into one `triage_records` plane.
- **0036** — $-Greek units + monetization conventions (raw is truth, dollar is derived).
- **0038** — By-Greek PnL attribution (`ScenarioAttribution`) — transcript §7.
- **0048** — Per-side vol surfaces (R2): fit put/call/combined; `surface_side` in the grid PK; combined is the reference; put−call IV spread = signal + QC.
- **0049** — Named historical scenarios (2008/COVID) compose as compound `Scenario`s repriced through the pricer; the correlation family reprices through `basket_variance` (a second path, built but dormant until ρ̄ exposure is real); additive-when-non-empty construction hash. *(extends 0006)*
- **0050** — RT-Vega (running-time / annualised vega) = `vega/√T`; per strike, raw + cash, T→0 guarded to 0.
- **0051** — Return to the blueprint: dispersion ρ̄ is a **realized-vol diagnostic** (Eq. 23 on constituent realized vols from bars), **not** a single-name-options trade. Retires constituent-option capture + the `constituent_top_n` capture gate; dissolves the throughput "emergency" and the permanent option-history loss. *(supersedes 0045, partially 0044)*
- **0052** — QC coverage to the blueprint: interior pinned tenors are **interpolated** (Eq. 22), edge/illiquid tenors (10d, 2y/3y LEAPs) are a **labelled low-confidence/unusable fallback** (`05-math-notes`) — not a hard per-tenor floor. Coverage = ≥95% ratio over monitored maturities (`14-slos`); `calendar_sanity` pages CRITICAL only on a **gross** inversion. Kills the SX5E false-critical; no capture-path change.
- **0054** *(Proposed)* — Per-currency risk-free **`r(T)` curve ingest** (`rates` table, as-of): the external curve is the **risk** rate Rho bumps against; the parity-implied rate stays the **pricing-consistency** rate; implied−riskfree spread = diagnostic + QC. Coherent with landed `ForwardConfig.rate` + Eq. 5 (`r` input, `q` derived). Unblocks task R1 (`infra-rates-curve-ingest`).

## Execution & booking
- **0043** — A booked fill is a **concrete contract**, resolved at booking time (grid-cell ticket → `(strike, expiry, right)` + paper mark). *(the booking chain seam)*
- **0047** — Password-gated booking write barrier: scrypt gate (env-configured, fail-closed) + a separate append-only decision log.

## Strategy, universe & capture
- **0044** — Top-N-by-weight dispersion selector (`top_n_by_weight`) + `dispersion_top_n` config (S1 precondition).
- **0045** — Constituent option capture: one underlying-generic basket, top-N membership seam.
- **0046** — The Strategy spine: typed contract, `Strategy` protocol, and the `strategy_id` identity stamp.

## Frontend
- **0030** — Visualization/UI stack: Plotly.js charts; shadcn/ui + TanStack Table.

---
**Removed (git history; clauses live on elsewhere):** 0022 (M5 vendored slice — reversed by 0023/0042),
0005 (analytics-core test surface → 0004), 0007 (no-dual-path → 0025/0027), 0008 (read-only → 0024 + code),
0013, 0014, 0016 (YAGNI), 0018 (layering → import-linter/map), 0020, 0021 (frozen pricing → 0004), 0029
(contract field names). To recover one: `git log --all --diff-filter=D -- .agent/decisions/`.
