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

> **▶ The destination: [`TARGET.md`](../TARGET.md)** — the owner's end-state capability map
> (position/risk, P&L attribution incl. Rho/Vanna/Volga + residual, surface engine, stress,
> execution/OMS, portfolio analytics, backtesting, strategy allocation) and the current
> end-of-week goal. New work is designed by diffing TARGET against this board.

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

> **▶ Audit remediation index (2026-06-11):**
> [`AUDIT-ACTION-PLAN-2026-06-11.md`](AUDIT-ACTION-PLAN-2026-06-11.md) is the single ordered
> worklist for the 101 audit findings (folds the REP rows + new groups, OQ rulings applied).
> The full report is [`AUDIT-POST-CAPTURE-backend-2026-06-11.md`](AUDIT-POST-CAPTURE-backend-2026-06-11.md).
>
> **▶ Intent-vs-delivery audit remediation (2026-06-12):**
> [`AUDIT-INTENT-VS-DELIVERY-REMEDIATION.md`](AUDIT-INTENT-VS-DELIVERY-REMEDIATION.md) is the ordered
> worklist from the [intent-vs-delivery audit](AUDIT-INTENT-VS-DELIVERY-2026-06-12.md) (config drift
> + green-gate≠correct class). **#1 [T-qc-residual-units](T-qc-residual-units.md)** (active — absolute-$
> forward/parity QC thresholds always-FAIL on a 7400-pt index, masks QC health + pollutes the coverage
> panel). Also active: [T-surface-arbfree-qc](T-surface-arbfree-qc.md) (RMSE-only gate, blind to the
> arb flags the vol-surface lane propagated). Latent mines: [T-capture-config-coherence](T-capture-config-coherence.md),
> [T-scenario-rate-axis](T-scenario-rate-axis.md), [T-pricing-config-completeness](T-pricing-config-completeness.md).
> Tenor/delta/vol-surface/coverage-panel findings already landed/in-progress — not re-opened.
> Parallel lanes are claimed below — **disjoint files; anything touching the SPX-hotfix files
> (`infra-ibkr/.../cp_rest_close_capture.py`, `live_capture.py`, `collectors/__init__.py`,
> `orchestration/eod_planning.py`, `universe/calendar_resolver.py`) waits until that hotfix lands.**

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|| — ✅ cleared 2026-06-11 | **Landed & verified, rows retired:** server-deploy slice-A scripts (`68d2c1f`/`91abdd7`); REP3/9/10 specs written; **V1** smoke driver (`e742bb2`); **QA-FIX** `fix/live-spine-wiring` now **merged**; **2A** (`b2b6a06`), **2B** (`d7a18f8`→`b947a63`), **1A** membership (`abdfd36`/`c019513`), **1F** — specs archived. Plus prior week: T-bridge (`28ab59c`), front fallback (`780ba85`), ingestion ADRs 0039/0040/0041, 429 backoff (`0c255ba`), data-root (`a2707e8`). | 2026-06-11 | see commits/ADRs |
| ✅ SPX hotfix LANDED | `hotfix/spx-post-close-guard` `07c892d` — session-bounded post-close guard + loud drop-100% (+ F-UNI-01 leap-day). Cherry-pick `07c892d` for a clean PR. | 2026-06-11 | **Deploy before tonight's 22:45 XNYS fire.** Unblocks the XEUR timer shift → `clock-timer-coherence.md`. |
| 🟢 index-only refactor — Phases 1-3 LANDED (claude, 2026-06-13) | **[T-index-only-refactor](T-index-only-refactor.md)** — the app is index-options-only, EuroStoxx-50-first. **P2** SPX parked `enabled:false` (`7cc6d73`); **P1** Saxo+Deribit packages removed (91 files, leaf adapters, zero core coupling; OAuth front defait; ADRs 0013/0014 superseded; `ddc3019`); **P3** `UniverseConfig.underlyings` removed — index registry is the single universe source (`c5b456c`, universe golden regen d41c8d2d→881ef3c6 by design). Single-name tickers stay index *constituents*, never option underlyings. **Gate: ruff ✓ mypy ✓ (209) lint-imports ✓ pytest ✓** — only the 3 pre-existing `documentation/`-deletion failures remain (owner: leave). Detailed removal log in the task. | 2026-06-13 | **P4 DONE** — `T-capture-config-coherence` folded (orphan `capture.yaml` deleted, `829eb19`; capture span single-sourced in `universe.yaml`). All 4 phases landed. Residual: delta-step-2 grid (audit An-6) is with a parallel agent. |
| ✅ Audit lanes LANDED | `audit-fixes-batch1`: STORAGE F-STORE-01/03 (`445d1ac`), RISK F-RISK-01/02/03 (`ba9dd26`), BFF F-BFF-01/02 (`059a9e8`). Gate 983/0/16. **REP1 = won't-fix** (content-hash). | 2026-06-11 | merge/cherry-pick the 3 to main |
| ✅ vol-surface correctness LANDED | `audit-fixes-batch1`: F-SURF-01 (DF flat-forward interpolation at the pinned tenor + `discount_factors_by_tenor` label binding, `PROJECTION_VERSION` 1.1.0, golden regen), SVI degeneracy propagated (`bound_hits`/`converged` → contract + BFF + a visible smile flag, `degeneracy_reasons` policy = flag-not-reject), F-BFF-03 (null holes + `has_holes`), F-BFF-04 (`axis_type` + `moneyness_buckets`). Spec [`T-vol-surface-correctness`](T-vol-surface-correctness.md). Gate 1371/0/16 + web 34/0, look-ahead clean. **Downstream half only** — 1m…3y stay labeled gaps until [T-tenor-selection](T-tenor-selection.md) + re-capture. | 2026-06-12 | merge with the batch |
| ✅ capture lane LANDED | **[T-tenor-selection](T-tenor-selection.md)** `74d2cc7` — tenor-targeted **bracket** expiry selection (replaces nearest-N). `select_expiries_bracketing`/`tenor_target_dates`/`bracket_dates` in `chain_planning.py`; month-token bracket `_select_discovery_months`/`_parse_month_token` + `_selection_from_config(config, as_of)` in `cp_rest_close_capture.py`; both stages (discovery + capture) + `plan_chain` wired; tests `test_tenor_selection.py` (12) + `test_tenor_discovery_months.py` (4). **Gate green 1371/16.** | 2026-06-12 | **ROOT CAUSE** of F-SURF-01 + SVI degeneracy — fixed at source. **Needs a re-capture to bank real 1m…3y.** |
| 🟢 vincent+claude (coverage panel) — built, placement pending | **[T-capture-coverage-panel](T-capture-coverage-panel.md)** — BFF `/api/coverage` **landed `f140a0f`** (`routers/coverage.py` + `app.py` register); front `CoverageTable`/`CoveragePanel` **landed `d972295`** (`components/CoverageTable.tsx`, self-contained, web 39/39). Verified on real 2026-06-11: shows 8 expiries all-10d + every tenor red. **REMAINING: drop `<CoveragePanel underlying tradeDate />` into `Market.tsx` (Tab 1) — overlaps codex front lane, needs that one coordinated edit.** | 2026-06-12 | BFF + component done; only Market.tsx placement left |
| ✅ delta window BUILT (claude, 2026-06-12) — gate green, awaits re-capture | **[T-delta-window](T-delta-window.md)** — `_DISCOVERY_STRIKES_PER_SIDE=16` (±~1%) **clipped the 30Δ band** the prof asked for (30Δ at +99 pts/10d → +1370/3y vs ±78 captured; `delta_band_completeness` QC=FAIL). **FIXED:** discovery strike window is now delta-driven + tenor-aware. `discovery_working_vol` seed in `StrikeSelectionConfig` + `configs/universe.yaml`; `discovery_delta_bound`/`select_discovery_strikes` in `chain_planning.py` (reuse `select_strikes_delta_band` at a ~20Δ margin — one delta source); `_qualify_strikes_for_expiry` replaces `_nearest_strikes(…,16)` in `cp_rest_close_capture.py` (`_DISCOVERY_STRIKES_PER_SIDE` removed; `DiscoveryRunawayError` valve). **Owner ruling:** full-30Δ, **cap=None** (a cap IS the bug we killed); fail-loud runaway guard, not a cap; single conservative seed. **Universe config-hash golden regenerated by design** (ADR 0028 / C7; pre-capture, no banked record). Tests: norm.ppf oracle vs engine, superset proof, tenor widening, runaway/garbage-vol raises, look-ahead clean. **Gate 1404/0/16.** | 2026-06-12 | **DONE pending re-capture** (owner-gated). `delta_band_completeness` should clear for covered tenors on the next capture. Disjoint from codex/coverage front lanes. |
| 📋 SPEC READY — intent-vs-delivery audit | **[T-intent-vs-delivery-audit](T-intent-vs-delivery-audit.md)** — systematic hunt for the **"green gate ≠ correct output"** class (a policy's intent silently clipped by a technical bound/count/default; tests check mechanism not delivered economics). Seeds: tenor + delta + the `surface_fit_error`-passes-on-degenerate-fit blind spot. Method + fan-out lanes + deliverable format in-spec. **Audit only, findings → tasks.** Reframes the 101-finding audit that missed these roots. | 2026-06-12 | for dispatched agents; QC triage (3 fails = ultra-short root) folded in as context |
| ✅ OHLC constituent backfill LANDED | [ohlc-constituent-backfill](ohlc-constituent-backfill.md) **DONE**: 548/553 underlyings serve 1y daily bars (spot-checked magnitudes: LVMH 493€, Airbus 176€, Boeing 222$). `4bd6536` (one-pass presence scan — the real stall — + `--max-windows`), `7c7a202` (venue-aware conid resolution + BFF reload watcher scoped to `apps/`+`packages/`; old wedged BFF killed by owner, relaunched healthy). Owner-authorized purge+refetch of the 70 wrong-conid names done (21 211 partitions). **Labeled gaps:** `NDA FI` (no HEX data permission); `DG`/`DTE`/`EL` cross-index ticker collisions → **OQ-10** (SPX company wins the key; SX5E shows the US homonym for those 3). | 2026-06-12 | merge with the batch |
| ✅ maintainability REP batch LANDED (claude) | [AUDIT-maintainability-2026-06-12](AUDIT-maintainability-2026-06-12.md) **implemented**: 17 commits `d9cd767..f4e8aa7` on `audit-fixes-batch1`, 3 waves of agent lanes + adversarial review. CI gate+justfile; BFF on FastAPI DI/pydantic; web on msw + ApiError + chartTheme; IBKR pydantic wire models (byte bar held) + close-capture split; **Saxo tick-routing bug fixed** (same-strike ticks landed on first expiry); SQLAlchemy metadata tier; one z-score/hashing/backoff/logging/WS-listener home each; core.paths bootstrap; scripts/ in gate; contracts plane on pydantic behind 101 golden pins (proven non-tautological vs pre-batch worktree); dead code −1338 LOC; test infra: shared builders, --regen-golden, FakeCpTransport. Gate: ruff+mypy(231)+lint-imports+web 65/65 green; pytest 1651+ passed, only the 3 pre-existing documentation/-deletion failures remain (owner: leave). **Ops notes:** eod-capture@ journal lines are now one-line JSON (configure_logging at entrypoints); EodResult.skipped and its done-line key are gone; snapshot warm-up counts sentinel-only rows COLD (deliberate; dead wings poll longer); babysitter alarm now latches (no reauth hammering — protects the SMS-2FA line). | 2026-06-13 | merge with the batch |
| ✅ maintainability audit round 2 LANDED (claude) | [AUDIT-maintainability-2026-06-12](AUDIT-maintainability-2026-06-12.md) — 20-agent fan-out (13 finders / 6 verifiers / 1 synthesis): 60 raw → 45 confirmed findings beyond REP0–REP10, ranked M1–M46 + REP11–REP22 stubs + 1 owner ruling (contracts-plane mini-pydantic). **M1 corrected post-audit:** "live preload bug" was a false positive — invisible raw `\x1f` byte in `constituentHistory.ts` fooled finder+verifier; hazard fixed (JSON keys + body-asserting test, web 49/49, lint clean). | 2026-06-12 | audit doc + 1 surgical web fix; rest is findings only |
| ✅ Claude (anthony) — DONE, gate green, awaiting commit | **2B on-demand stress surface** — `frontend/basket_scenarios.py` (new), `routers/basket.py`, `serializers.py`, web `Basket.tsx` / `stressApi.ts` / `api.ts` / `components/StressSurface.tsx` (new) + tests | 2026-06-12 | `POST /api/basket/scenarios` — full-reprice (spot×vol) surface for a composed basket, no cron. Reconstructs valuations from the stored grid (DF backed out of the stored price), reuses `infra.risk.stress_surface`. Gate green (ruff/mypy/lint-imports/pytest + web 38/0), live-smoked on SPX 06-11. **Note: SPX `instrument_master` multiplier=1.0 (capture quirk) → $ figures per-contract×1.** |
| 🔵 Claude (anthony) — IN PROGRESS | **Basket/Risk tab operator-flow fixes** — `routers/basket.py` (empty `trade_date` → latest banked analytics day) + API tests; web `pages/Basket.tsx`, `pages/RiskScenarios.tsx` (drop the duplicated `StressBasketComposer` — on-demand stress lives on the Basket tab) | 2026-06-12 | Owner report: Basket price/stress broken from the default UI flow (empty date → 400; stale BFF process 500s on `/api/basket/scenarios` config reload), Risk tab duplicated Basket |
| HELD (do after deps) | REP6 config (determinism), full REP2 as-of (look-ahead), Web REP3/4/9/10, connectivity bundle, **ADR-0040 mega-fix → [`T-raw-invariant.md`](T-raw-invariant.md)** | — | see [action plan](AUDIT-ACTION-PLAN-2026-06-11.md) waves 3-5 |


## What's next — the index-analytics build

The detailed per-workstream `tasks/` specs (C-series style) are written **per phase as Phase 0
closes**, per [`roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md) §6. The
sequence; **Phase 0 and Phase 1 are now fully specced** (per-workstream files, linked below), behind the library ADRs [0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)–[0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md). Critical path: P0 → 1J → 1A+1B → 1C → 1F → 1G+1H → 1I (1J foundational — index registry; 1D gated on P0.4, parallel; 1E folded into P0/1C).

> **▶ Priority + parallelism (owner, 2026-06-07).** A functional front (1I) on the real foundation is the **main goal** — but it is **not** a blocking gate: **advance every non-conflicting downstream task in parallel** (fan out agents). Only same-file/same-contract conflicts serialize.
> **Week sprint:** **Mon 2026-06-08 (open)** — the daily **forward capture must RUN**, stacking gap-free history (critical path **D1 → 1C → 1G**). **Fri 2026-06-12** — platform **functional**: full dashboard (1I) + working **stress-test page** (Phase 2) + a **start of page 3** (Phase 3); first two pages operational; infra running continuously, good enough to read the market and make trade decisions.

> **▶ Priority update (owner, 2026-06-10) — the daily capture now RUNS automatically.** The Mon
> "capture must RUN" goal is **met**: the 429 transport backoff (`0c255ba`) + overwrite re-fire
> ([ADR 0041](../.agent/decisions/0041-eod-refire-overwrites-rather-than-skips.md), `962809f`) make
> the two **active** systemd-user timers (`eod-capture@XEUR` 16:15 UTC SX5E / `@XNYS` 20:45 UTC SPX)
> each land raw+snapshots+surfaces — validated live, both indices. **Depends only on the IBKR
> gateway being up.** **Near-term priority order:** (1) **confirm ≥2 clean days banked** (Wed+Thu)
> for the Fri review; (2) **[POST-CAPTURE-backend-audit](POST-CAPTURE-backend-audit.md)** — owner-
> requested full backend audit, run once days are banked; (3) **[daily-bar-compaction](daily-bar-compaction.md)**
> (rulings recorded, ready) + **[ohlc-constituent-backfill](ohlc-constituent-backfill.md)**; (4)
> **T-raw-invariant #1/#2** after QA-FIX lands; (5) Fri platform — front 1I (main goal) + stress
> page + page-3 start. **Obsolete:** the "clear ledger + partitions before the real close" /
> intraday-dry-run-purge guidance — ADR 0041 makes the real close **overwrite** an intraday run, so
> no manual purge is needed any more.

> [!IMPORTANT]
> **ABSOLUTE PRIORITY FOR TOMORROW'S PROFESSOR REVIEW (Fri 2026-06-12):**
> 1. **Data Quality & EOD Capture:** Verify that tonight's EOD captures (SX5E/SPX) are 100% clean and fully populated (no 429 drops, no empty derived tables).
> 2. **BFF/Projection Fixes:** Correct F-BFF-04 (fallback axis moneyness keys) and F-SURF-01 (flat discount factor rates to zero) so that the vol surface / Greeks display correctly.
> 3. **Constituent Charts:** Launch the [ohlc-constituent-backfill](ohlc-constituent-backfill.md) so constituent candlestick charts are not empty.
> 4. **CDC Page 1 Reflow:** Complete Page 1 front-end phases 1-3 ([front-page1-cdc-buildout](front-page1-cdc-buildout.md): reading-order reflow, smile side-by-side, 2D heatmap) to look fully compliant with the cahier des charges.


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
**violations** + one gap) and the ADRs. **Three ADRs accepted** (owner ruled OQ-A…D, 2026-06-10):

| ADR (accepted) | Task | Covers | Status |
|------|------|--------|--------|
| [0039 raw-schema bridge + sample regen](../.agent/decisions/0039-raw-schema-bridge-and-sample-regeneration.md) | [T-bridge](T-bridge.md) | dual-schema + hand-made samples | ✅ **DONE** (`28ab59c`) — closes the [ADR 0021](../.agent/decisions/0021-analytics-core-merge.md) deferral; SX5E sample now needs only a gateway capture |
| [0041 re-fire overwrites not skips](../.agent/decisions/0041-eod-refire-overwrites-rather-than-skips.md) | — (in `pipeline.py`) | the `(trade_date,stage)`-only ledger making the 2nd per-calendar timer skip its index | ✅ **DONE** (`962809f`) — both timers now capture; self-heals intraday pollution |
| [0040 ingestion persistence invariants](../.agent/decisions/0040-ingestion-persistence-invariants.md) | [T-raw-invariant](T-raw-invariant.md) | raw-before-derived guard (#1/#2); complete-or-flagged + per-run completion (#3/#4 → **fold into QA-FIX** per OQ-D) | **QUEUED** — high care; #1/#2 sequenced after QA-FIX (overlaps `run_state`/`collectors`/`cp_rest_close_capture`/`storage/adapter`). 0041 already covers the multi-timer facet. |

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
- **[Page-1 CDC build-out](front-page1-cdc-buildout.md) — queued (2026-06-10), unclaimed.** Tab-1 base is
  shipped (`ad97c6c`: control bar+QC badge, index/constituent history, 3D surface, smile, dollar-Greeks term
  structure). Missing the rest of the cahier des charges: **vol scorecards (§3.3)**, **nappe heatmap (§3.4)**,
  **ATM term structure (§3.5)**, **Greeks-vs-strike shape cards (§3.6)**, global maturity selector. Phased; 2+3
  are data-backed today, 5 waits on path A, colours out of scope until the §6 styling pass.
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
