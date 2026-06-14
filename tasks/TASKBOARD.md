# TASKBOARD

Collision guard for a shared `devs`-group workspace where several humans and agents work
at once. **Before you change files, claim them in the claim table below; clear your claim
when done.** It is advisory, not enforced — it only works if everyone reads and writes it.
The real safety is branch discipline: one branch per task, merge small and often, so
collisions surface as merge conflicts, never silent overwrites.

What was *built* and *why* lives in the code, the per-directory `README.md`s, and the ADRs
in [`.agent/decisions/`](../.agent/decisions/) — not here. This board is only "who is
touching what right now" and "what is open to pick up." Finished specs move to
[`tasks/archive/`](archive/); the changelog is git history.

> **▶ The destination is [`TARGET.md`](../TARGET.md)** — the single roadmap: the end-state
> capability map and the ordered build sequence (§7 is the pre-ordered gap list). New work is
> designed by diffing TARGET against this board.

## Scope guardrail

Scope + universe model live in ADR [0042](../.agent/decisions/0042-index-options-only-scope-ibkr-sole-broker.md)
and [`TARGET.md §0`](../TARGET.md) — **index-options-only, IBKR sole broker, SX5E sole live index**.
The reminder that earns its place here: if any older spec, ADR, or README still says "Saxo",
"Deribit", "three brokers", or "equity underlying", **the index-only pivot wins** — do not resurrect
it. The gate (the only one) is in `AGENTS.md`; **green** 2026-06-14 after the core-fleet
integration (1911 passed, 12 skipped; web lint + 82 vitest + 20 e2e green).

## Active claims

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| claude (matthieu) | core-fleet integration → `main`: `packages/{execution,strategy}`, `apps/frontend` (attribution + booking + orders-reconcile), `infra/universe`, IBKR top-N capture; 8 specs archived | 2026-06-14 | done — booking chain + strategy spine + attribution view landed, gate green |
| claude (matthieu) | [infra-per-side-surfaces](archive/infra-per-side-surfaces.md) (R2 infra core) — branch `infra-per-side-surfaces`: `infra/{surfaces,contracts,risk,qc,actor,orchestration}`, `execution/concretization`, `apps/frontend` BFF grid readers (combined-default), ADR 0048, goldens | 2026-06-14 | done — `surface_side` ∈ {put,call,combined} in grid PK; combined byte-identical; put−call IV spread signal+QC; gate green (1924 passed). Front toggle → frontend-per-side-surfaces-toggle |
| claude (matthieu) | [infra-basket-leg-per-side-routing](archive/infra-basket-leg-per-side-routing.md) — branch `infra-basket-leg-per-side-routing`: `infra/contracts` (`BasketLeg.surface_side`), `infra/risk/multileg`, `apps/frontend` BFF `basket_scenarios`, ADR 0048 follow-up, baskets golden | 2026-06-14 | done — basket legs route to their named wing (default combined → no change); shared `index_rows_by_cell_and_side`/`resolve_cell_side`; missing wing = labelled gap; booking stays combined; gate green (1934 passed) |
| claude (matthieu) | [infra-signal-layer](archive/infra-signal-layer.md) — branch `infra-signal-layer`: new `packages/infra/src/.../signals/*`, `infra/contracts/{tables,registry,__init__}.py` (`StrategySignal`/`strategy_signals`), new `packages/strategy/src/.../signal_data.py`, infra+strategy READMEs, contracts-plane fixture+golden, tests | 2026-06-14 | done — R3 ρ̄ (inverse Eq-23) + IV-rank/RV−IV/term-slope persisted daily as-of, look-ahead clean; `signal_snapshot_from_store` reads them back into S1's `SignalSnapshot` (ρ̄ entry now live); gate green (2026 passed) |
| claude (matthieu) | [strategy-s1-dispersion](archive/strategy-s1-dispersion.md) — branch `strategy-s1-dispersion`: new `packages/strategy/src/.../{s1_dispersion,dispersion_data}.py`, strategy README, 2 test files | 2026-06-14 | done — S1 strategy object: ρ̄ entry, top-N point-in-time straddles routed put/call (first ADR-0048 consumer), delta-flattening synthetic short-forward leg, net-vega kill; store-backed adapter; gate green (1960 passed) |
| Claude (vincent) | [T-front-currency-and-bands](T-front-currency-and-bands.md) — front display wiring (`api.ts`, `DollarGreeks.tsx`, `MaturityAccordion`, `format.ts`) + un-hardcode `BasketLegGrid` band list | 2026-06-13 | backend `/api/indices` currency single-source already landed; front half remains |
| claude (matthieu) | [core-projection-moneyness-grid](archive/core-projection-moneyness-grid.md) — branch `core-projection-moneyness-grid`: `core/platform_config` + `configs/pricing.yaml` (typed field), `infra/actor/{driver,nautilus_host}`, `infra/orchestration/reconstruction/batch` (wiring) | 2026-06-14 | done — `SurfaceConfig.moneyness_buckets` typed home; entry points resolve from config at the `run_analytics_with_qc` choke; `DEFAULT_MONEYNESS_BUCKETS` literal retired; zero-churn default (only `pricing` hash moved); gate green (1934 passed) |
| claude (matthieu) | [core-pricing-config-completeness](archive/core-pricing-config-completeness.md) (deferred slices 2&3) — branch `core-pricing-config-completeness`: `core/config/platform_config` (`SurfaceConfig.model`/`fallback_model`, `ForwardConfig.max_candidate_count`/`outlier_method`/`max_robust_zscore`), `infra/surfaces/fit`, `infra/forwards/estimate`, `infra/utils/robust`, `configs/pricing.yaml`, config + forwards/surfaces/robust tests, contracts-plane golden | 2026-06-14 | done — surface method labels + forward-engine candidate/outlier policy typed; zero-churn defaults (only `pricing` hash moved); reconciled the `strategy_signals` as_of:null golden; gate green (2057 passed) |
| Claude (anthony) | Basket/Risk tab operator-flow fixes — `routers/basket.py` (empty `trade_date` → latest banked day), web `pages/Basket.tsx`, `pages/RiskScenarios.tsx` | 2026-06-12 | drop the duplicated stress composer from the Risk tab; on-demand stress lives on Basket |

## Layer ownership (planning pass, 2026-06-13)

Six per-layer planning agents diffed [`TARGET.md`](../TARGET.md) against this board and took
ownership of their lanes. **Ownership is encoded in the filename prefix** — `core-`, `infra-`,
`ibkr-`, `strategy-`, `execution-`, `frontend-` — matching the package layers
`core ← infra ← infra-ibkr ← {strategy, execution} ← apps/frontend`. The ready queue below is
grouped by that prefix; the prefix *is* the claim. These are lane-ownership claims, **not**
work-in-progress file locks — claim a specific file in the table above before you edit it.
Cross-layer seams (one spec, steps in several layers) are split into per-layer specs that link
their dependency. Two collisions during the pass resolved to the broker leaf and the config
spine: the capture tasks went `ibkr-`, the config-home tasks went `core-`.

A **seventh `platform-` lane** (2026-06-14) owns the cross-cutting work that maps to no product
layer — CI/CD, deployment, security review, operational chores, and live audits. It is a
backlog-ownership lane, *not* a package (nothing in `packages/`/`apps/` corresponds to it).
Two completed audits (`T-task-coherence-audit`, `T-repo-file-hygiene-audit`) were archived to
[`tasks/archive/`](archive/) in the same pass.

## Ready queue — unclaimed, pick one and claim a row above

Disjoint lanes; anything touching the same file/contract serializes. TARGET §7 is the authority
on order. Grouped by owning layer; each item links its full spec. **★ = new spec from the
planning pass.**

**`core-` — config & lineage spine (level 0)**
- [core-explicit-rate-config](core-explicit-rate-config.md) — **step 1 landed** (typed `ForwardConfig.rate` home + Eq-5 carry-split override, zero-churn `null` default; open = `forward_curve` contract/display, `r(T)` curve; the compute-wiring slice is infra's)
> **Landed & archived (2026-06-14):** [core-pricing-config-completeness](archive/core-pricing-config-completeness.md) (ADR 0028 — the two deferred slices: `SurfaceConfig.model`/`fallback_model` give the surface-fit method choice a typed home and `fit_slice` reads the labels from config instead of the `METHOD_*` literals — fallback honestly named `nonparametric`, not the blueprint's `spline`; `ForwardConfig.max_candidate_count`/`outlier_method`/`max_robust_zscore` give the forward engine's candidate-cap + outlier policy a typed home in the existing `forward:` block, with `robust.outlier_flags` parameterised by `rejection_z` so the shared util stays decoupled. Zero-churn defaults — None-cap / `mad` / 3.5 = byte-identical fits & forwards; only the `pricing` config-hash moved. `outlier_method: none` and `max_candidate_count` are real opt-in behaviours, tested).
> **Landed & archived (2026-06-14):** [core-projection-moneyness-grid](archive/core-projection-moneyness-grid.md) (ADR 0028 — `SurfaceConfig.moneyness_buckets` gives the surface-projection log-moneyness grid a typed `pricing.yaml` home; the projection chain resolves it from config at the `run_analytics_with_qc` choke and the `DEFAULT_MONEYNESS_BUCKETS` `.py` literal is retired; zero-churn default, only the `pricing` config-hash moved).
> **Landed & archived (2026-06-14):** [core-config-effective-dating](archive/core-config-effective-dating.md) (§0/ADR 0028 — the as-of/effective-dated half: a bundle may date itself with `effective_from`, and `load_platform_config(configs/, as_of=D)` / `from_config(…, as_of=D)` refuse a bundle effective *after* D, so a past-day replay can never silently pick up config authored later; `ProvenanceStamp`/`stamp()` carry the resolved `as_of`, folded into `stamp_hash` only when set. Zero-churn — current path byte-identical, pinned stamp golden unchanged; the additive-nullable `as_of` provenance column regenerated the contracts-plane golden rows. Reach-back to an older in-force version stays the infra profile store's `resolve_as_of`).

**`infra-` — analytics / risk / surface / storage compute**
- [infra-rates-curve-ingest](infra-rates-curve-ingest.md) (R1) · [infra-mirror-greeks-putcall](infra-mirror-greeks-putcall.md) (greeks-only; *not* the per-side fit)
- [infra-rt-vega](infra-rt-vega.md) (#5)
- [infra-strike-window-pct-clip](infra-strike-window-pct-clip.md) (latent mine — labelling + delivery test) · [infra-daily-bar-compaction](infra-daily-bar-compaction.md) (971k one-row `daily_bar` files)
- ★ [infra-named-scenarios-and-corr-shock](infra-named-scenarios-and-corr-shock.md) (§5.4 — named historical stress 2008/COVID + correlation-shock axis; reuses the 2B grid + landed rate-axis pattern)
- ★ [infra-tail-risk-var-es](infra-tail-risk-var-es.md) (§5.9 — VaR/ES off the full-reprice distribution + liquidity/concentration; **post-week**, substrate built) · ★ [infra-residual-diagnosis](infra-residual-diagnosis.md) (§7 #10 — regress the attribution residual against unmodeled exposures; **deferred**, gated behind booking + banked realized P&L)

> **Signal layer (2026-06-14):** [infra-signal-layer](archive/infra-signal-layer.md) **landed** — R3 average
> implied correlation ρ̄ (inverse Eq-23, closed-form) + IV-rank/percentile, RV−IV, term-slope, computed off the
> as-of surfaces/bars/weights and persisted as `strategy_signals` (new `StrategySignal` contract, layer `signals`,
> provider-partitioned). Pure math + as-of orchestrator (`infra/signals/`), blind to alpha. The strategy reads them
> back via `strategy/signal_data.py::signal_snapshot_from_store` — **S1's ρ̄ entry is now live, not fixture-fed**.
> Open follow-ons: daily batch wiring at the `run_analytics` choke; ρ̄ coverage-bias hardening; the realized-corr kill.
> **Landed & archived (2026-06-14, infra-coverage audit):** [infra-pnl-attribution](archive/infra-pnl-attribution.md) (2C by-Greek attribution), [infra-second-order-greeks](archive/infra-second-order-greeks.md) (Vanna/Volga/Charm + Rho/Vanna/Volga + realized day-over-day — compute landed), [infra-scenario-rate-axis](archive/infra-scenario-rate-axis.md) (rate-shock engine+config landed). The front remainders live in [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md) and [frontend-scenario-rate-axis-wiring](frontend-scenario-rate-axis-wiring.md).
> **R2 per-side surfaces (2026-06-14):** [infra-per-side-surfaces](archive/infra-per-side-surfaces.md) **infra core landed** (ADR 0048 — per-side fit, `surface_side` grid PK, put−call IV spread signal + QC), then [infra-basket-leg-per-side-routing](archive/infra-basket-leg-per-side-routing.md) **landed** — `BasketLeg.surface_side` opt-in routes the summed basket + BFF reprice to each leg's wing (booking stays combined). Front remainder: [frontend-per-side-surfaces-toggle](frontend-per-side-surfaces-toggle.md).

**`ibkr-` — IBKR capture lane & connectivity**
- [ibkr-option-volume-capture](ibkr-option-volume-capture.md) (#7)
- [ibkr-clock-timer-coherence](ibkr-clock-timer-coherence.md) (the live SX5E/XEUR timer shift)
- ★ [ibkr-unattended-reauth](ibkr-unattended-reauth.md) (§5.9 — close the ~daily SMS-2FA wall; OAuth bring-up + SSO-expiry ALARM delivery. **Load-bearing for the unattended-week story**)
- ★ [ibkr-broker-account-read](ibkr-broker-account-read.md) (§5.9/§6 — **found by the 2026-06-14 IBKR-coverage audit**: the read-only CP-REST positions/cash/fills path reconciliation needs; the recon sub-lane of [execution-operational-hardening](execution-operational-hardening.md) assumes it but no `ibkr-` task owned it)

**`strategy-` — the strategy book, signals, backtester** (`packages/strategy` spine **landed** — [strategy-contract-base](archive/strategy-contract-base.md): typed contract + protocol + 4-context harness + `strategy_id` stamp; the S-specs and backtester build on it.)
- ★ [strategy-s2-index-put-line](strategy-s2-index-put-line.md) (§3 S2) · ★ [strategy-s3-gamma-trading](strategy-s3-gamma-trading.md) (§3 S3)
- ★ [strategy-s4-covered-strangle](strategy-s4-covered-strangle.md) (§3 S4) · ★ [strategy-s5-calendar-carry](strategy-s5-calendar-carry.md) (§3 S5, optional)
- [strategy-composition](strategy-composition.md) (Phase 2, §5.8 — the §3 book composed: combined Greeks + stress + attribution + correlation view; infra/risk + BFF + web are seams)
- [strategy-backtester](strategy-backtester.md) (§7.8) · ★ [strategy-decorrelation-analytics](strategy-decorrelation-analytics.md) (§5.8 — decorrelation *verification*, post-week; depends on [strategy-composition](strategy-composition.md))

> **Landed & archived (2026-06-14):** [strategy-s1-dispersion](archive/strategy-s1-dispersion.md) **done** — the flagship S1 dispersion object (`DispersionStrategy` + store-backed `StoreBackedDispersionData`): ρ̄-rich entry, point-in-time top-N straddles routed put/call (the first ADR-0048 per-side consumer), a delta-flattening synthetic short-forward index leg, net-vega-collapse kill. v1 forward-only; v2 (short index straddle) deferred. The ρ̄ source ([infra-signal-layer](archive/infra-signal-layer.md)) **landed** — S1's entry now reads the persisted signal layer (live, not fixture-fed). v2 (short index straddle) remains the open lane upgrade.
>
> **Landed & archived (2026-06-14):** [strategy-delta-hedge-band](archive/strategy-delta-hedge-band.md) **done** — the shared band rebalance rule (course req #9, "Delta-hedge en bande"): a typed `DeltaHedgeBand` (target, economic-config `half_width` tolerance, `hedge_ratio` convention) + a pure `decide_delta_hedge` that holds within the band and re-hedges only on band exit, sized to return delta to target. S1's `rebalance` now delegates to it (byte-identical: target 0, ratio −1), the inline copy removed; S3/S4 share the same rule when they land.

**`execution-` — OMS / booking chain (`packages/execution` now built: concretize → book → fills-store → audit, paper-gated)**
- ✓ The §7 #1 booking chain **landed 2026-06-14 (core-fleet)**: [execution-fill-concretization](archive/execution-fill-concretization.md) (grid-cell → concrete priced paper fill, ADR 0043) → [execution-booking-commit](archive/execution-booking-commit.md) (password-gated write barrier) → [execution-fills-position-store](archive/execution-fills-position-store.md) (fills-based book read by risk/attribution). 3A ticket landed prior → [archive](archive/execution-order-ticket.md).
- [execution-order-sign-and-send](execution-order-sign-and-send.md) (3B broker send — read-only / paper until an explicit owner gate; **off this week**)
- [execution-operational-hardening](execution-operational-hardening.md) (§7.9 umbrella — margin / kill switch / broker recon / alert delivery; margin sub-lane gates S2, rest post-week)

**`frontend-` — BFF + web delivery (apps/frontend)**
- [frontend-page1-cdc-buildout](frontend-page1-cdc-buildout.md) (vol scorecards, nappe heatmap, ATM term structure, Greeks-vs-strike cards) · [frontend-sigfig-scientific-display](frontend-sigfig-scientific-display.md) (#6)
- [frontend-capture-coverage-panel](frontend-capture-coverage-panel.md) (capture-quality table; BFF + `CoverageTable` landed **and the panel drop is mounted** at `Market.tsx:172` — only the phase-2 quote-completeness add remains)
- ★ [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md) (step 3 of infra-second-order-greeks; after 3A + sigfig) · ★ [frontend-scenario-rate-axis-wiring](frontend-scenario-rate-axis-wiring.md) (BFF/front slice of infra-scenario-rate-axis)
- ★ [frontend-per-side-surfaces-toggle](frontend-per-side-surfaces-toggle.md) (R2 front half — put/call/combined toggle on the 3D surface + smiles, put−call IV-spread view; infra core landed ADR 0048)
- ✓ **Landed 2026-06-14 (core-fleet):** [frontend-attribution-view](archive/frontend-attribution-view.md) (§7 #2 — BFF router + attribution waterfall over `ScenarioAttribution`, wired on Basket) · [frontend-orders-booking-reconcile](archive/frontend-orders-booking-reconcile.md) (§7 #1 coherence — dead `Orders.tsx` retired, `/orders` redirects to the one booking home on Basket)

**`platform-` — CI/CD, deploy, security, ops & audits (cross-cutting; not a package)**
- [platform-security-review](platform-security-review.md) (pre-live-order pass; auth/secrets/BFF/deps runnable now, order-seam §2 opens with 3A/3B)
- [platform-intent-vs-delivery-audit](platform-intent-vs-delivery-audit.md) (all-layers audit — "green gate ≠ correct output"; findings → tasks) · [platform-post-monday-restore-cleanup](platform-post-monday-restore-cleanup.md) (one-shot — purge the Friday-restore run-state ledger AFTER Monday 2026-06-15 close is captured + validated)
- ★ [platform-secret-and-dep-scan](platform-secret-and-dep-scan.md) (the gate gap both CI and security disowned — secret + dep-vuln scan + pre-commit) · ★ [platform-deploy-stack-ownership](platform-deploy-stack-ownership.md) (govern the real systemd/CP-REST/babysitter/alert stack that landed untracked; carries the deferred compose decision)
- ★ [platform-data-durability](platform-data-durability.md) (backup/restore for the irreplaceable `data/` raw store + ledger — no backup exists) · ★ [platform-doc-coherence-fix](platform-doc-coherence-fix.md) (**owner ruling: `documentation/` is dead** — re-point the 11 tasks that still read it, then quarantine/delete the tree)
- **Archived this pass (2026-06-14):** [platform-ci-pipeline](archive/platform-ci-pipeline.md) **done** (landed as `.github/workflows/gate.yml`, exceeds spec — 3 jobs) · [platform-server-deploy-plumbing](archive/platform-server-deploy-plumbing.md) **superseded** by R4 (CP-REST, not TWS socket); real deploy stack now owned by `platform-deploy-stack-ownership`

**Context hygiene**
- [T-agent-context-minimization](T-agent-context-minimization.md) — Part A (`.agent/` minimum-vital refactor) is partly landed; the `.agent/decisions/` index + glossary trim continue.

## Blocked / parked — do NOT start

- **[1D-futures-term-structure](1D-futures-term-structure.md)** — parked (ADR [0037](../.agent/decisions/0037-futures-capture-deferred-forward-only.md), futures deferred forward-only). The index-only pivot does not re-open it.
- **[T-raw-invariant](infra-raw-invariant.md)** — the ADR-0040 raw-before-derived guard (#1/#2); sequenced after the live-spine wiring it overlaps.
- **REP7 (nautilus-connectivity)** needs a live `TradingNode`; **REP8 (IBKR LST)** needs IBKR live-auth. Specs were retired to git history with the other REP files; revive from history if revisited.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-13 | short intent |`
