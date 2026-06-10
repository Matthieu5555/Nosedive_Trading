# TASKBOARD

The in-repo collision guard for a shared workspace with several humans and agents
working at once. **Before you start changing files, claim them here.** Clear your
claim when you're done. This is advisory, not enforced — it only works if every
actor reads and writes it, which is why `AGENTS.md` tells you to. What we actually
care about is the **working tree on the server staying clean**: everything canonical
under `packages/` and `apps/`, one tree, one gate. Branches are optional convenience,
not the goal.

When a task is finished, clear its row. The record of *what* was built and *why*
lives in the code, the per-directory READMEs, and the ADRs in `.agent/decisions/`;
finished task specs move to [`tasks/archive/`](archive/).

## Current phase: build the index options-analytics pipeline

> **▶ START HERE: [`documentation/roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md)
> is the plan of record for what we build next.** It sequences the work Phase 0 → Phase 3:
> index → point-in-time constituents → delta-band option chain → IV / surface / Greeks
> (decimal + dollar) → daily-close cron → operator front page, then Tab-2 risk/strategy, then
> an execution sketch. Read [`vision-medium-term.md`](../documentation/vision-medium-term.md)
> for the *why* and [`.agent/open-questions.md`](../.agent/open-questions.md) for the decisions
> behind it (OQ-1…6 are all ruled). The blueprint (`documentation/blueprint/`, ADR 0011) still
> overrides on any domain/formula conflict.

**Ground truth (2026-06-06):** the full root gate is **green** —
`uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` →
**805 passed, 16 skipped, 0 failed**, ruff clean, mypy clean (173 files), import-linter 2/2.
This is the **only** gate. There is one tree (`packages/` + `apps/`); the old flat `backend/`
is gone.

### The merge/convergence is closed

This repo and Vincent's independent build of the same system
(`github.com/Vincent-20-100/AlgoTrading`) were merged toward the max-union of both. That work
is **done**: the layered uv-workspace monorepo is the chassis, **Nautilus is the runtime spine**
([ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)), and the
whole system — core, the frozen `contracts` seam, storage, the analytics core, risk, QC/validation,
the Nautilus-hosted actor, orchestration/observability, and the three broker leaf adapters
(IBKR via Nautilus + a custom Client-Portal REST transport; Saxo + Deribit via our own adapters)
— is canonical in `packages/infra` with one frontend in `apps/frontend`. The four headline
acceptance tests (byte-identical replay, provenance, reconstruction, handover) run inside the root
gate. Vincent's repo is checked out read-only at **`Vincent's Code/`** (gitignored, not canonical)
as a source of inspiration; refresh with `git -C "Vincent's Code" pull`.

The convergence tasks **C1–C6 and C8** all landed and merged to `main`; their specs are in
[`tasks/archive/`](archive/) as the historical record, alongside the original A–E backbone and the
M0–M10 merge fan-out. The closed convergence runbook is
[`archive/CONVERGENCE-PLAN.md`](archive/CONVERGENCE-PLAN.md). **What remains from convergence is
C7 only** (config hardening — the standard landed as [ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md),
but most of the application is unbuilt; see below). The IBKR-REST course requirement landed
([ADR 0024](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md) accepted); its evaluation
record is [`archive/ibkr-rest-api-evaluation.md`](archive/ibkr-rest-api-evaluation.md).

## In flight

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| claude/Matthieu | `.env.example`, `scripts/ibkr_bootstrap.py`, `documentation/connectivity/**` | 2026-06-06 | server-deploy plumbing (non-compute); does **not** touch C7 / `core/config/**` — see [tasks/server-deploy-plumbing.md](server-deploy-plumbing.md) |
| Vincent (Claude) | `tasks/REP3` (edit) + `tasks/{REP9,REP10}-*.md` (new) — **doc-only** | 2026-06-07 | dashboard-review follow-ups (course transcript + [AUDIT-tasks-coherence](AUDIT-tasks-coherence-2026-06-07.md)): REP3 += fitted-SVI smile overlay + decimal/$ co-equal Greeks; REP9 registry-driven index picker (BFF `/api/indices` over 1J); REP10 Tab-1/Tab-2 nav IA before Phase 2. **No code.** |
| Matthieu (Claude) | **V1** — NEW `scripts/smoke_e2e.py` + `packages/infra/tests/test_smoke_e2e.py` (driver-honesty tests only; owns no compute) | 2026-06-07 | e2e smoke: bootstrap → SAMPLE replay → analytics/grid → BFF HTTP (no 500s) → web build/test, single PASS/FAIL/SKIP summary, exit 0/1/2. Drives existing public entrypoints only; **off** all dirty shared files. See [tasks/V1-e2e-verification-smoke.md](V1-e2e-verification-smoke.md). (2C landed `4e3f50f`, on `main`/`perso`.) |
| Vincent (Claude) | **2B — ✅ DONE (end-to-end, routed).** Commits: `d7a18f8` (core engine `infra/risk/stress_surface.py` + config `StressSurfaceConfig`/`scenarios.yaml` ±50%), `fe1138a` (BFF `surface` view on `GET /api/risk/scenarios` + seam tests), `d935910` (actor persists surface cells EOD; golden 50→77), `7005d4c` (web `pages/Stress.tsx` + `stressApi.ts` Plotly surface), `b947a63` (wired route+nav into the shell after 2A landed). **Vertical complete: config → compute/persist → BFF → routed page.** Full gate green (ruff/mypy 201/lint-imports 2/2/pytest 1231; web eslint+vitest 35/35+tsc+build). Only **optional cosmetic tidy** left (non-blocking, noted in `stress_surface.py` docstring): unify `effective_surface_version`→`effective_scenario_version` + export surface syms from `risk/__init__`. Row clearable. | 2026-06-07 | ±50%/±50% cartesian (spot×vol) full-reprice stress surface — DONE. |
| Matthieu (Claude) | **2A + 1F-followup — DONE** (branch `fix/live-spine-wiring`). **2A `b2b6a06`**: contracts `BasketLeg`/`Basket`, `infra/risk/multileg.py`, BFF `routers/basket.py`+serializer, web basket page/grid/panel/templates. **1F `8f71fb5`**: `projection.py` emits the ATM-put pillar `atmp` (same ATM strike as `atm`; option right from label suffix) so the straddle template is the genuine two legs `[atm,atmp]`; `PROJECTION_AXES_VERSION`→1.1.0; golden `projected_option_analytics.json` regenerated (additive). Full gate green both sides. **Uncommitted leftovers (ride with concurrent work):** my 1-line nav in `AppLayout` (Codex restyle), this board row. Row clearable once merged. See [2A](2A-basket-builder.md) · [1F-atm-put-cell](1F-atm-put-cell.md). | 2026-06-07 | multi-leg basket + true two-leg ATM straddle (ATM-put cell). |
| Vincent (Claude) | **T-bridge (ADR 0039) — ✅ DONE (full gate green), uncommitted.** NEW `universe/sample_bridge.py` (`events_to_contracts`/`contracts_to_events`; placed in `universe/` not `storage/` to avoid a storage↔universe cycle — ADR placement note) + `universe/__init__.py` export + `scripts/export_sample.py` rewritten to **write** (verified on real AAPL: 33 events, `reconstruct_sample` byte-for-byte round-trip) + `notebooks/demo_pipeline_ibkr.ipynb` inline conversion → bridge (proven 909-events-identical on ASML) **+ notebook de-rot** (demo_config `surface`/`forward`; `reconstruct_day` `config_hash`→`config_hashes`) so replay runs end-to-end + NEW `packages/infra/tests/test_sample_bridge.py` (4). ruff/mypy/import-linter 2/2 clean; full pytest gate green. **Disjoint from QA-FIX.** SX5E real sample now needs only a gateway re-capture of raw. **Also done-uncommitted:** front nappe fallback `apps/frontend/.../routers/analytics.py` + `apps/frontend/tests/test_readback_api.py`. | 2026-06-10 | raw-schema bridge + sample regen — DONE; nappe SX5E front fallback shipped. |
| Matthieu (Claude) | **QA-FIX** (branch `fix/live-spine-wiring`) — audit-driven bug/quality pass. Files (partitioned, disjoint per agent): risk+pricing dollar-greek 100× fix + 3 red tests (`pricing/engine.py`, `pricing/dollar_greeks.py`, `risk/{greeks,basket,scenarios}.py`, `tests/test_{pricing,risk,scenario}.py`); storage silent-empty read (`storage/adapter.py`, `tests/test_storage.py`); run-state ledger lock (`orchestration/run_state.py`); open-session close capture (`collectors/{live,replay,normalize}.py`, `infra-ibkr/.../cp_rest_close_capture.py`); QC live-wiring + triage persist (`orchestration/{eod_runner,qc_job}.py`); ADR-0028 QC-threshold config migration (`qc/thresholds.py`, `validation/anomaly.py`, `configs/qc.yaml`, `core/.../platform_config.py`); hygiene (`.gitignore`, `.agent/map.md`, `pyproject.toml`, `AGENTS.md`, `core/.../log.py`). | 2026-06-08 | fleet code-review remediation; restores green gate + fixes latent correctness bugs. |


## What's next — the index-analytics build

The detailed per-workstream `tasks/` specs (C-series style) are written **per phase as Phase 0
closes**, per [`roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md) §6. The
sequence; **Phase 0 and Phase 1 are now fully specced** (per-workstream files, linked below), behind the library ADRs [0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)–[0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md). Critical path: P0 → 1J → 1A+1B → 1C → 1F → 1G+1H → 1I (1J foundational — index registry; 1D gated on P0.4, parallel; 1E folded into P0/1C).

> **▶ Priority + parallelism (owner, 2026-06-07).** A functional front (1I) on the real foundation is the **main goal** — but it is **not** a blocking gate: **advance every non-conflicting downstream task in parallel** (fan out agents). Only same-file/same-contract conflicts serialize.
> **Week sprint:** **Mon 2026-06-08 (open)** — the daily **forward capture must RUN**, stacking gap-free history (critical path **D1 → 1C → 1G**). **Fri 2026-06-12** — platform **functional**: full dashboard (1I) + working **stress-test page** (Phase 2) + a **start of page 3** (Phase 3); first two pages operational; infra running continuously, good enough to read the market and make trade decisions.

Spec rows:

| When | Work | Spec |
|------|------|------|
| **✅ Done (100% — gate open)** | **C7 — config hardening.** Tasks 1–5 **and both carry-forwards** landed: six Part VII YAMLs + bundle loader; every hashed economic param in validated typed config (no `.py` literals at the audited sites); per-bundle `config_hashes` on every stamp; injected code identity + per-run config freeze + `validate_manifest`; broker.yaml bands/backoff wired; and the **effective-dated profile store** on SQLite (`ProfileRepository`/`resolve_as_of`). Both halves locked: replay-a-run **and** replay-a-past-day-fresh. The owner prerequisite — *no new compute until params are in YAML and reproducibility is locked* — is fully met. Spec archived. | [archive/C7-config-hardening.md](archive/C7-config-hardening.md) |
| **Data foundation (pre-equity-scale)** | **Data architecture fixed** — Parquet record + DuckDB/Polars query + SQLite metadata, no Postgres in core (ADRs [0015](../.agent/decisions/0015-storage-repository-port-tiered-backends.md)/[0017](../.agent/decisions/0017-provider-dimension.md)/[0019](../.agent/decisions/0019-one-immutable-raw-model.md)/[0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)/[0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)/[0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md)). One foundational impl task: **D1 — `provider` partition segment** (0017 gap) — ✅ **landed**. | [archive/D1-storage-foundation.md](archive/D1-storage-foundation.md) |
| **Phase 0** — ✅ **done** | Pinned the tenor grid + $-Greek units/flags; built the IBKR historical-bar fetch (underlying daily OHLC); futures-capture decided (deferred, forward-only — [ADR 0037](../.agent/decisions/0037-futures-capture-deferred-forward-only.md)). | [archive/P0-contracts-and-unblockers.md](archive/P0-contracts-and-unblockers.md) |
| **Phase 1 (Tab 1)** — ✅ **1A–1J done (gate-green)** | Shipped: 1J index registry → 1A membership → 1B Δ-band → 1C capture → 1F projection → 1G cron → 1H QC → 1I front+API, plus the P0 contracts and the D1 `provider` partition foundation. All specs archived to [`archive/`](archive/). **Still GATED, not started: [1D futures term-structure](1D-futures-term-structure.md)** — blocked on P0.4 (futures-capture decision); kept in this dir. 1E folded into P0/1C. | per-WS → [archive/](archive/) |
| **Phase 2 (Tab 2)** — *Fri 2026-06-12, parallel-OK* | Basket builder → stress/scenario (±50% spot/vol) → PnL attribution by Greek → strategy composition. **Engine already built** (infra/risk scenario + ADR 0006; BFF `/api/risk[/scenarios]`) — mostly wiring + UI on the built engine, parallelisable now. Specs: [2A](2A-basket-builder.md) · [2B](2B-stress-scenario.md) · [2C](2C-pnl-attribution.md) · [2D](2D-strategy-composition.md). | this dir |
| **Phase 3 (start)** — *Fri: a beginning* | Execution sketch: ticket → sign (email) → send. **Read-only / paper until an explicit owner gate.** Specs: [3A](3A-order-ticket.md) · [3B](3B-order-sign-and-send.md). | this dir |
| **Cross-cutting (parallel)** | End-to-end verification + smoke ([V1](V1-e2e-verification-smoke.md)); CI gate on push/PR ([ci-pipeline](ci-pipeline.md)); pre-execution security review ([security-review](security-review.md)). | this dir |
| **Library leverage (REP backlog)** | Lean harder on declared libs; delete hand-rolled plumbing (audit: [AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md)). **Ready:** [REP0 deps](REP0-dependency-hygiene.md) · [REP1 scipy](REP1-scipy-micro-swaps.md) · [REP2 as-of](REP2-storage-asof-unification.md) · [REP3 frontend](REP3-frontend-tanstack.md) · [REP4 shadcn](REP4-shadcn-decision.md) · [REP5 pydantic-BFF](REP5-pydantic-bff-contract.md) · [REP6 pydantic-config](REP6-pydantic-config-layer.md). **Blocked:** [REP7 nautilus-connectivity](REP7-nautilus-connectivity-collapse.md) (live `TradingNode`) · [REP8 IBKR LST](REP8-ibkr-lst-exchange.md) (IBKR live-auth). Analytics core stays bespoke — do not swap. | this dir |
| **Phase 3 (sketch)** | Execution: ticket → sign (email) → send. Read-only/paper until an explicit owner gate. | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 3 |

### Ingestion-stack remediation (audit 2026-06-10) — **ADRs accepted, tasks queued**

An ingestion-stack audit (2026-06-10) found the raw layer is not durably guaranteed: two
`RawMarketEvent` schemas coexist with no bridge, raw-landing is a conditional side-effect across
five divergent persist entrypoints, `persist_outputs` silently skips empty tables, and the run
ledger is per-stage. Observed fallout: **SX5E 2026-06-10 persisted every derived table but no
`raw_market_events` and no `projected_option_analytics`.** Reconciled against the blueprint (three
**violations** + one gap) and the ADRs. Two ADRs **accepted (owner ruled OQ-A…D, 2026-06-10)** and
two task specs queued:

| ADR (accepted) | Task | Covers | Note |
|------|------|--------|------|
| [0039 raw-schema bridge + sample regen](../.agent/decisions/0039-raw-schema-bridge-and-sample-regeneration.md) | [T-bridge](T-bridge.md) | dual-schema + hand-made samples | closes the [ADR 0021](../.agent/decisions/0021-analytics-core-merge.md) bridge deferral; **low collision**; unblocks a committable SX5E sample |
| [0040 ingestion persistence invariants](../.agent/decisions/0040-ingestion-persistence-invariants.md) | [T-raw-invariant](T-raw-invariant.md) | raw-before-derived (#1/#2); complete-or-flagged + per-run completion (#3/#4 → **fold into QA-FIX** per OQ-D) | **high care; #1/#2 blocked-by/sequenced-after QA-FIX** (overlaps `eod_runner`/`run_state`/`collectors`/`cp_rest_close_capture`/`storage/adapter`) |

Before writing any test, read [TESTING.md](TESTING.md) — the shared test-surface contract and the
converged seam → contract-test map. Code without the named tests is not done.

## Known carried-forward items

- **Data retention + cold-compaction (build-when-measured).** Per [ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md):
  the blueprint Part XV 4-tier retention is **policy**, not yet enforced (nothing deletes data, and at
  current scale nothing should). The scale lever for the small-files problem is **cold-compaction by
  ticker** (merge old `(date, underlying)` files into `(underlying, month|year)`) — built **only when a
  measured threshold is crossed** (adding SP500, or file-count/query-latency past a bound), never
  speculatively. Non-blocking; lives behind the `StorageRepository` port over cold data only.
  **▶ THRESHOLD NOW CROSSED (2026-06-10):** S&P500 backfill landed → `daily_bar` is **419 755 files /
  4.9 GB for ~20 MB of real data** (1 row/file, ~250× overhead). Spec written:
  [daily-bar-compaction](daily-bar-compaction.md) — **rulings recorded (OQ-1…4, 2026-06-10: 1 file/ticker,
  hot/cold cold-only compaction, archive-then-delete, daily_bar only); ready, awaiting go on implementation.**
- **[OHLC constituent backfill](ohlc-constituent-backfill.md) — queued (2026-06-10), unclaimed.** Per-component
  candlesticks are empty for most names (all SX5E EU, NVDA…) — only index underlyings + some US names have
  `daily_bar`. Root cause diagnosed: `ohlc_backfill.py` over the **attended Gateway** is impractically slow
  (the `conid=0` warmup 503 + data-farm transient 503s → ~3 names/10 min), though the raw Gateway history
  endpoint is fast for real conids (curl-verified). Fix the warmup/503 handling + clean the SX5E seed
  (drop the `VGM6` future, verify EU conid resolution), then run for SPX+SX5E. Coordinate with daily-bar-compaction.
- **[H1 — repo-hygiene audit](archive/H1-repo-hygiene-audit.md) — ✅ landed (2026-06-06, against `e0ab3ab`).**
  Read-only classification done; report at [H1-repo-hygiene-report.md](archive/H1-repo-hygiene-report.md).
  Outcome: **no tracked dead paths** (nothing to `git rm`). Applied safe patch — added the five
  missing tool-cache patterns to `.gitignore` (`.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`,
  `.hypothesis/`, `.import_linter_cache/`; all were untracked) and `rmdir`'d the two empty
  never-tracked merge stubs `.agents/` and `.codex/`. `Test Lenny/`, `Vincent's Code/`, `ThomasOssen/`
  left in place and flagged. Gate green. `Vincent's Code/` on-disk removal stays a `matthieu`/admin
  step (see below).
- **[H2 — doc reconciliation](archive/H2-doc-reconciliation.md) — ✅ landed (2026-06-06, against `e0ab3ab`).**
  Report: [H2-doc-reconciliation-report.md](archive/H2-doc-reconciliation-report.md). The **gate-wired
  freshness guard** is in (`packages/infra/tests/test_doc_freshness.py`, 33 cases: README coverage,
  symlink resolution, map routes every area, no dead doc links). Audited all 18 infra-module + 8
  package READMEs; fixed five concrete drifts (four C7 "param is now typed config, not a `.py`
  literal" in forwards/iv/surfaces/risk + execution's layer position). Glossary gained the merge
  terms (config bundle, push collection seam / `RawCollector`). `map.md` verified current; the docs
  index gate command fixed (it omitted `lint-imports`). The blueprint data-dictionary vs code
  contract field-name split was raised as OQ-7 and **ruled (owner, 2026-06-06): follow the
  blueprint, code conforms** — the six field renames (`forward_price`/`implied_vol`/`log_moneyness`/
  `scenario_pnl`/`qc_status`/`dollar_*`) landed across contracts + producers/consumers + tests +
  docs; data starts fresh so no migration. See
  [ADR 0029](../.agent/decisions/0029-contract-field-names-conform-to-blueprint.md). Gate green
  incl. the new check.
- **`Test Lenny/`** is a throwaway standalone experiment, not canonical and imported by nothing. Now
  **untracked** — removed from the repo (`git rm --cached`) and gitignored, **kept on disk** for the
  admin to delete. Its README flags it as ignore-me. So it is excluded from any GitHub push.
- **`Vincent's Code/` removal** was blocked during C5 (the dir is owned by `matthieu`; the C5 process
  ran as `vincent`, so `rm` was permission-denied — the clone is intact). It is gitignored and not
  canonical, so it does not affect the gate; its README should be flagged as reference-only (see the
  banner queued for `matthieu` to apply). Remove it as `matthieu` (then drop its `pyproject`/
  `.gitignore` excludes) whenever convenient. Not blocking.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-06 | short intent |`
