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

> **⛔ Autonomous-run guardrail (2026-06-16) — read before picking up work overnight.**
> 1. **Ground in the reference:** every design/scope decision traces to `docs/blueprint/` + the
>    course transcripts `docs/transcripts/` (esp. `…Conseils-front-end.txt`,
>    `…Greeks-et-strategies-vol.md`). Read them first; don't ask the owner what they already specify.
> 2. **Do NOT revert recent owner+Claude decisions:** **ADR 0051** (index + constituent *prices*
>    only, realized-vol ρ̄), the **page-1 reading model** (`frontend-page1-reading-model.md`), and
>    this hygiene pass. Load-bearing. (≠ Matthieu's `c665614` page-1 rebuild, which is being
>    *corrected*, not preserved.)
> 3. **Page-1 = ONE agent at a time** on `Market.tsx`/`charts.tsx`/`pages/market/*`. The
>    reading-model is the umbrella; `cdc-buildout` is superseded (heatmap + accordion dropped).
>    Do not spawn parallel page-1 agents.
> 4. **Risk / stress / scenarios = ONE agent at a time.** `frontend-scenario-rate-axis-wiring`,
>    `frontend-second-order-greeks-panels`, and `strategy-composition` all touch the same shared
>    files (`infra/risk/scenarios.py`, `StressSurface.tsx`, BFF `serializers.py`/`risk.py`,
>    `RiskScenarios.tsx`). Serialize them — do not run them in parallel. The PnL/stress **compute
>    substrate is already landed** (by-greek attribution, 2nd-order greeks, rate axis, named +
>    correlation, 2A/2B); these are front/BFF wiring slices over it.
> 5. **`frontend-named-scenarios-wiring` is NOT pickable** — its named half landed; only the
>    correlation axis remains and it is **gated/dormant** (no real ρ̄ exposure on the live book —
>    do **not** fabricate one). Blocked until a real `BasketCorrelationExposure` lands.

## Active claims

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| _none_ | — | — | — |

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

> **✅ RESOLVED — capture integrity (2026-06-15 SX5E canary, run_id
> `89421177611f42ff85b55ba9144f8662`).** The canary exposed three risks — junk banked as a close, a
> lane dropped silently, capture too slow. Outcome: **quote integrity** landed (refuse
> last-only / market-closed captures — *stays real*); **throughput + constituent-lane** were
> *dissolved* by [`ADR 0051`](../.agent/decisions/0051-return-to-blueprint-dispersion-realized-vol-diagnostic.md)
> — the blueprint never asked to capture constituent *options*, so the close reverts to **index
> options + constituent prices** and ρ̄ is a realized-vol diagnostic. The amputation
> ([`blueprint-return-dispersion-diagnostic`](archive/blueprint-return-dispersion-diagnostic.md))
> **landed 2026-06-16, gate green** — full-membership ρ̄ (Eq. 23), capture lane + knobs removed.

**`core-` — config & lineage spine (level 0)**
- [core-explicit-rate-config](core-explicit-rate-config.md) — **step 1 landed** (typed `ForwardConfig.rate` home + Eq-5 carry-split override, zero-churn `null` default; open = `forward_curve` contract/display, `r(T)` curve; the compute-wiring slice is infra's)
> **Landed & archived (2026-06-14):** [core-pricing-config-completeness](archive/core-pricing-config-completeness.md) (ADR 0028 — the two deferred slices: `SurfaceConfig.model`/`fallback_model` give the surface-fit method choice a typed home and `fit_slice` reads the labels from config instead of the `METHOD_*` literals — fallback honestly named `nonparametric`, not the blueprint's `spline`; `ForwardConfig.max_candidate_count`/`outlier_method`/`max_robust_zscore` give the forward engine's candidate-cap + outlier policy a typed home in the existing `forward:` block, with `robust.outlier_flags` parameterised by `rejection_z` so the shared util stays decoupled. Zero-churn defaults — None-cap / `mad` / 3.5 = byte-identical fits & forwards; only the `pricing` config-hash moved. `outlier_method: none` and `max_candidate_count` are real opt-in behaviours, tested).
> **Landed & archived (2026-06-14):** [core-projection-moneyness-grid](archive/core-projection-moneyness-grid.md) (ADR 0028 — `SurfaceConfig.moneyness_buckets` gives the surface-projection log-moneyness grid a typed `pricing.yaml` home; the projection chain resolves it from config at the `run_analytics_with_qc` choke and the `DEFAULT_MONEYNESS_BUCKETS` `.py` literal is retired; zero-churn default, only the `pricing` config-hash moved).
> **Landed & archived (2026-06-14):** [core-config-effective-dating](archive/core-config-effective-dating.md) (§0/ADR 0028 — the as-of/effective-dated half: a bundle may date itself with `effective_from`, and `load_platform_config(configs/, as_of=D)` / `from_config(…, as_of=D)` refuse a bundle effective *after* D, so a past-day replay can never silently pick up config authored later; `ProvenanceStamp`/`stamp()` carry the resolved `as_of`, folded into `stamp_hash` only when set. Zero-churn — current path byte-identical, pinned stamp golden unchanged; the additive-nullable `as_of` provenance column regenerated the contracts-plane golden rows. Reach-back to an older in-force version stays the infra profile store's `resolve_as_of`).

**`infra-` — analytics / risk / surface / storage compute**
- ★ **[T-capture-tenor-bracket-rewire](T-capture-tenor-bracket-rewire.md) (P1 — latent P0 vs SPX)** — `38910d9` reverted the tenor-bracketing wiring; `_selection_from_config` falls to nearest-N (`max_expiries=64`) and silently drops LEAPs on any >64-expiry chain. From the intent-vs-delivery audit (F1). Same module as `infra-strike-window-pct-clip` — serialize.
- [infra-rates-curve-ingest](infra-rates-curve-ingest.md) (R1 — **needs an ADR + blueprint amendment first; not pickable yet**) · ~~[infra-mirror-greeks-putcall](archive/infra-mirror-greeks-putcall.md)~~ **done & archived**
- ~~[infra-rt-vega](archive/infra-rt-vega.md)~~ **done & archived** (#5)
- [infra-strike-window-pct-clip](infra-strike-window-pct-clip.md) (latent mine — labelling + delivery test) · ~~[infra-daily-bar-compaction](archive/infra-daily-bar-compaction.md)~~ **done & archived**
- ~~★ [infra-named-scenarios-and-corr-shock](archive/infra-named-scenarios-and-corr-shock.md)~~ **done & archived** (§5.4 — front wiring remainder: [frontend-named-scenarios-wiring](frontend-named-scenarios-wiring.md))
- ★ [infra-tail-risk-var-es](infra-tail-risk-var-es.md) (§5.9 — VaR/ES off the full-reprice distribution + liquidity/concentration; **post-week**, substrate built) · ★ [infra-residual-diagnosis](infra-residual-diagnosis.md) (§7 #10 — regress the attribution residual against unmodeled exposures; **deferred**, gated behind booking + banked realized P&L)
- ★ **[infra-surface-fit-quality](infra-surface-fit-quality.md) (P1 — the "weird nappe" root, found by the 2026-06-15 live render)** — ⚠️ the obvious fix (gate `fit_slice` on `arb_free`/`bound_hits`/`converged`) was **prototyped and reverted** as a blueprint deviation (`dbc05c6`, 2026-06-15); read the corrected spec, not this line. Remaining work is three lanes: (1) upstream data hygiene / tighter quote QC + IV-solver bounds + `min_points_per_slice`, verified on settled-close data; (2) front robustness to degenerate slices (was folded into the now-archived page-1 buildout — re-home onto the landed reading model); (3) longer-term owner-gated fallback routing. The ADR-0052 QC recalibration (`f1a6205`) landed part of lane 1.

> **Signal layer (2026-06-14):** [infra-signal-layer](archive/infra-signal-layer.md) **landed** — R3 average
> implied correlation ρ̄ (inverse Eq-23, closed-form) + IV-rank/percentile, RV−IV, term-slope, computed off the
> as-of surfaces/bars/weights and persisted as `strategy_signals` (new `StrategySignal` contract, layer `signals`,
> provider-partitioned). Pure math + as-of orchestrator (`infra/signals/`), blind to alpha. The strategy reads them
> back via `strategy/signal_data.py::signal_snapshot_from_store` — **S1's ρ̄ entry is now live, not fixture-fed**.
> Open follow-ons: daily batch wiring at the `run_analytics` choke; ρ̄ coverage-bias hardening; the realized-corr kill.
> **BFF read surface landed & archived 2026-06-16 (gate green):** [frontend-signals-bff](archive/frontend-signals-bff.md) — `GET /api/signals[/underlyings]` serializes the persisted `strategy_signals` per index and as-of (pydantic-edge house pattern, `strategy_signal_to_dict` + kind→label/unit map, `by_kind` index), recomputing nothing. Consumed by the web Signals page (F-SIG). Open: IV *percentile* persistence (only `iv_rank` is banked today, so it is not surfaced — read-only slice won't recompute it).
> **Landed & archived (2026-06-14, infra-coverage audit):** [infra-pnl-attribution](archive/infra-pnl-attribution.md) (2C by-Greek attribution), [infra-second-order-greeks](archive/infra-second-order-greeks.md) (Vanna/Volga/Charm + Rho/Vanna/Volga + realized day-over-day — compute landed), [infra-scenario-rate-axis](archive/infra-scenario-rate-axis.md) (rate-shock engine+config landed). The front remainders live in [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md) and [frontend-scenario-rate-axis-wiring](frontend-scenario-rate-axis-wiring.md).
> **R2 per-side surfaces (2026-06-14):** [infra-per-side-surfaces](archive/infra-per-side-surfaces.md) **infra core landed** (ADR 0048 — per-side fit, `surface_side` grid PK, put−call IV spread signal + QC), then [infra-basket-leg-per-side-routing](archive/infra-basket-leg-per-side-routing.md) **landed** — `BasketLeg.surface_side` opt-in routes the summed basket + BFF reprice to each leg's wing (booking stays combined). Front remainder: [frontend-per-side-surfaces-toggle](frontend-per-side-surfaces-toggle.md).

**`ibkr-` — IBKR capture lane & connectivity**
- ~~[EMERGENCY-quote-integrity-gate](archive/EMERGENCY-quote-integrity-gate.md)~~ **done & archived** — refuse to bank a last-only / market-closed capture; quarantine single-sided/zero-spread rows; enforce `completeness`/`flags`. **Quote integrity stays real under ADR 0051.**
- ~~[EMERGENCY-constituent-lane-activation](archive/EMERGENCY-constituent-lane-activation.md)~~ **retired (ADR 0051)** — the constituent-option lane it activated is gone; constituents are prices-only. Done-then-mooted.
- ~~[EMERGENCY-capture-throughput](archive/EMERGENCY-capture-throughput.md)~~ **dissolved (ADR 0051)** — the throughput emergency was an artifact of constituent-option capture, which no longer happens; the index's own chain is already in-window. Its three follow-ups (cross-underlying / snapshot-warmup / intraday-conid) are all retired below.
- ~~[ibkr-capture-cross-underlying-concurrency](archive/ibkr-capture-cross-underlying-concurrency.md)~~ **landed 2026-06-16 (`b128d6b`), now moot (ADR 0051)** — fan-out under one shared gateway budget; value remains only for the single index chain.
- ~~[ibkr-snapshot-warmup-concurrency](archive/ibkr-snapshot-warmup-concurrency.md)~~ **retired & archived 2026-06-16 (ADR 0051)** — constituent-walk throughput optimization; moot for index-only capture. Revive only if a future pure-implied dispersion re-opens single-name capture.
- ~~[ibkr-intraday-conid-cache](archive/ibkr-intraday-conid-cache.md)~~ **retired & archived 2026-06-16 (ADR 0051)** — same constituent-walk lane; moot for index-only capture.
- ~~[ibkr-option-volume-capture](archive/ibkr-option-volume-capture.md) (#7)~~ **done & archived** (branch `ibkr-option-volume-capture`, 2026-06-15)
- ~~[ibkr-clock-timer-coherence](archive/ibkr-clock-timer-coherence.md)~~ **done & archived** (guard hotfix live; XEUR timer 18:15→22:45 Berlin; drift test)
- ★ [ibkr-unattended-reauth](ibkr-unattended-reauth.md) (§5.9 — close the ~daily SMS-2FA wall; OAuth bring-up + SSO-expiry ALARM delivery. **Load-bearing for the unattended-week story**)
- ~~★ [ibkr-broker-account-read](archive/ibkr-broker-account-read.md)~~ **done & archived** (§5.9/§6 — read-only CP-REST positions/cash/fills; feeds the recon sub-lane of [execution-operational-hardening](execution-operational-hardening.md))

**`strategy-` — the strategy book, signals, backtester** (`packages/strategy` spine **landed** — [strategy-contract-base](archive/strategy-contract-base.md): typed contract + protocol + 4-context harness + `strategy_id` stamp; the S-specs and backtester build on it.)
- ~~★ [strategy-s4-covered-strangle](archive/strategy-s4-covered-strangle.md)~~ **done & archived** (§3 S4) · ★ [strategy-s5-calendar-carry](strategy-s5-calendar-carry.md) (§3 S5, optional)
- [strategy-composition](strategy-composition.md) (Phase 2, §5.8 — the §3 book composed: combined Greeks + stress + attribution + correlation view; infra/risk + BFF + web are seams)
- ~~[strategy-backtester](archive/strategy-backtester.md)~~ **landed & archived 2026-06-16** (§7.8 — research machine + prod-shadow / store adapter / txn-cost / `POST /api/backtest/run`, gate green 2384; **deferred tail = P&L-level shadow**, revive a stub when picked up) · ★ [strategy-decorrelation-analytics](strategy-decorrelation-analytics.md) (§5.8 — decorrelation *verification*, post-week; depends on [strategy-composition](strategy-composition.md))

> **Landed & archived (2026-06-14):** [strategy-s1-dispersion](archive/strategy-s1-dispersion.md) **done** — the flagship S1 dispersion object (`DispersionStrategy` + store-backed `StoreBackedDispersionData`): ρ̄-rich entry, point-in-time top-N straddles routed put/call (the first ADR-0048 per-side consumer), a delta-flattening synthetic short-forward index leg, net-vega-collapse kill. v1 forward-only; v2 (short index straddle) deferred. The ρ̄ source ([infra-signal-layer](archive/infra-signal-layer.md)) **landed** — S1's entry now reads the persisted signal layer (live, not fixture-fed). v2 (short index straddle) remains the open lane upgrade.
>
> **Landed & archived (2026-06-15):** [strategy-s2-index-put-line](archive/strategy-s2-index-put-line.md) **done** — the
> S2 index short-put line (`PutLineStrategy`, config-only — no data adapter): the deliberate opposite tail to S1. A
> rolling line that sells one ~25Δ ~30d index put/day, gated by `decide_sell` = the RV−IV signal (implied rich vs
> realized) **and** a config capacity cap (course's 30-open rolling line); the put routes to the put wing (ADR 0048) at
> the **steered** `put_delta_band` (the assignment-frequency lever). `decide_exit` flattens on a net-delta drawdown
> proxy and otherwise **defers to the execution kill switch** ([execution-operational-hardening](execution-operational-hardening.md),
> §5.9/§6 — S2 is its first consumer); `rebalance` is a no-op (S2 carries its short-put delta intentionally). Contract:
> short downside vega/gamma, positive theta, carried long delta. First [strategy-backtester](archive/strategy-backtester.md)
> target (§7.8, course 2021-vs-2008). Gate green (2141 passed).
>
> **Landed & archived (2026-06-15):** [strategy-s3-gamma-trading](archive/strategy-s3-gamma-trading.md) **done** — the
> S3 gamma-trading object (`GammaStrategy` + store-backed `StoreBackedGammaData`): IV-rank entry on the **cheapest**
> name (course "low IV expected to rise"), a long ATM call + delta-flattening **short stock** leg (Δ=0), the p.108
> scalp cycle via the shared `decide_delta_hedge` band rule (second consumer — stock hedge vs S1's synthetic forward),
> and the net-dollar-gamma-collapse kill. The cheap name + call delta resolve from the persisted signal layer + grid;
> spot is the grid forward (carry==0 ⇒ forward==spot). v1 builds the long-call/short-stock form (the put/long-stock
> mirror is the deferred symmetric variant). The S1/S3 low-realized-vol overlap is held on purpose (the book view must
> surface it), not fixed here. Gate green (2115 passed).
>
> **Landed & archived (2026-06-14):** [strategy-delta-hedge-band](archive/strategy-delta-hedge-band.md) **done** — the shared band rebalance rule (course req #9, "Delta-hedge en bande"): a typed `DeltaHedgeBand` (target, economic-config `half_width` tolerance, `hedge_ratio` convention) + a pure `decide_delta_hedge` that holds within the band and re-hedges only on band exit, sized to return delta to target. S1's `rebalance` now delegates to it (byte-identical: target 0, ratio −1), the inline copy removed; S3/S4 share the same rule when they land.

**`execution-` — OMS / booking chain (`packages/execution` now built: concretize → book → fills-store → audit, paper-gated)**
- ✓ The §7 #1 booking chain **landed 2026-06-14 (core-fleet)**: [execution-fill-concretization](archive/execution-fill-concretization.md) (grid-cell → concrete priced paper fill, ADR 0043) → [execution-booking-commit](archive/execution-booking-commit.md) (password-gated write barrier) → [execution-fills-position-store](archive/execution-fills-position-store.md) (fills-based book read by risk/attribution). 3A ticket landed prior → [archive](archive/execution-order-ticket.md).
- [execution-order-sign-and-send](execution-order-sign-and-send.md) (3B broker send — read-only / paper until an explicit owner gate; **off this week**)
- [execution-operational-hardening](execution-operational-hardening.md) (§7.9 umbrella — margin / kill switch / broker recon / alert delivery; margin sub-lane gates S2, rest post-week)

**`frontend-` — BFF + web delivery (apps/frontend)**
- 🔴 **[frontend-3onglets-consolidation](frontend-3onglets-consolidation.md) (P0 — land by Fri 2026-06-19)** — collapse the current 7 tabs to the owner-locked **3 onglets** (Données / Risque / Ordres) per the design contract [frontend-3onglets-target-ux.md](frontend-3onglets-target-ux.md). **ONE front owner, 4 sequential steps** (shell flip last); do NOT parallelize. Folds Risk+Positions→Risque, Strategy+recon→Ordres, drops Signals, demotes Operations.
- [frontend-bff-bidask-volume](frontend-bff-bidask-volume.md) (P1 — BFF-only, parallel-safe) — surface per-option **bid/ask + volume** in `/api/analytics`; prerequisite for the Onglet-1 price-structure block (transcript: bid/ask not mid).
- ✓ **Landed & archived 2026-06-16/17 (the page-1 cluster is settled — do NOT reopen):** [frontend-page1-reading-model](archive/frontend-page1-reading-model.md) (`c4ce734` — the owner-locked scrollable reading model: price → scorecards → 3D nappe → one tenor selector driving put/call smile + greeks → ρ̄) **supersedes** ~~[frontend-page1-cdc-buildout](archive/frontend-page1-cdc-buildout.md)~~ (heatmap + accordion dropped) and subsumes ~~[frontend-page1-cdc-0051-correction](archive/frontend-page1-cdc-0051-correction.md)~~ (`6605990` — the `c665614` rebuild misframing is corrected, not preserved). ~~[frontend-tab-shell](archive/frontend-tab-shell.md)~~ + ~~[frontend-signals-bff](archive/frontend-signals-bff.md)~~ also landed (gate green). **Open follow-on:** surface-fit front robustness (lane 2 of [infra-surface-fit-quality](infra-surface-fit-quality.md)) re-homes onto the landed reading model.
- [frontend-capture-coverage-panel](frontend-capture-coverage-panel.md) (capture-quality table; BFF + `CoverageTable` landed and mounted — only the phase-2 quote-completeness add remains. **NB: the mount moved when `c4ce734` rebuilt `Market.tsx`; re-confirm placement before phase 2.**)
- ★ [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md) — ⛔ **NOT pickable as-is: re-scope first.** `c4ce734` deleted the render targets it extended (`DollarGreeks`, `GreeksTermStructure`); the greeks now live in the page-1 tenor panel. Compute (Vanna/Volga/Charm) is banked but invisible — re-home onto the new greeks panel before building. · ★ [frontend-scenario-rate-axis-wiring](frontend-scenario-rate-axis-wiring.md) (**persisted `/api/risk/scenarios` rate sweep landed 2026-06-17, gate green**; only the on-demand **Basket** rate sweep remains — see spec)
- ★ [frontend-per-side-surfaces-toggle](frontend-per-side-surfaces-toggle.md) (R2 front half — put/call/combined toggle on the 3D surface + smiles, put−call IV-spread view; infra core landed ADR 0048)
- ✓ **Landed 2026-06-14 (core-fleet):** [frontend-attribution-view](archive/frontend-attribution-view.md) (§7 #2 — BFF router + attribution waterfall over `ScenarioAttribution`, wired on Basket) · [frontend-orders-booking-reconcile](archive/frontend-orders-booking-reconcile.md) (§7 #1 coherence — dead `Orders.tsx` retired, `/orders` redirects to the one booking home on Basket)
- ✓ **Landed 2026-06-15, archived:** [frontend-sigfig-scientific-display](archive/frontend-sigfig-scientific-display.md) (#6 — sci-notation @ 6 sig-figs + currency landed via the sci-notation + `T-front-currency-and-bands` passes; `lib/format.ts` `sci`/`sciUnit` is the single home). *(Stale active duplicates of the two attribution/orders specs were also removed this pass — the archived copies are canonical.)*

**`platform-` — CI/CD, deploy, security, ops & audits (cross-cutting; not a package)**
- [platform-security-review](platform-security-review.md) (pre-live-order pass) — **§1/3/4/5 + the landed half of §2 reviewed 2026-06-16 → [platform-security-review-report.md](platform-security-review-report.md)**: no CRITICAL/HIGH; one MEDIUM gates 3B (M2 — booking audit must be write-ahead of the fill write). Spec's 2026-06-07 baseline is stale (OAuth LST module, 3A ticket + paper booking chain, pycryptodome, and secret/dep-scan CI all landed; 3B live-transmit still absent). Re-open §2 when [execution-order-sign-and-send](execution-order-sign-and-send.md) lands.
- ~~[platform-intent-vs-delivery-audit](archive/platform-intent-vs-delivery-audit.md)~~ **done & archived 2026-06-16** — findings in [T-intent-vs-delivery-audit-findings-2026-06-16](T-intent-vs-delivery-audit-findings-2026-06-16.md). 7 confirmed (F1 latent nearest-N regression `38910d9` → now [T-capture-tenor-bracket-rewire](T-capture-tenor-bracket-rewire.md); F2 long-end QC floor; F3 surface_fit measured_value; F4 forward cap; F5 scenario clip; F6 ADR-0028 literals; F7 storage re-capture visibility) + 2 overturned false positives. F2–F7 tracked in the findings doc (no separate files). · ~~[platform-post-monday-restore-cleanup](archive/platform-post-monday-restore-cleanup.md)~~ **done & archived 2026-06-17** (5 synthetic 06-12 ledger rows purged; backup kept)
- ~~★ [platform-secret-and-dep-scan](archive/platform-secret-and-dep-scan.md)~~ **done & archived** · ★ [platform-deploy-stack-ownership](platform-deploy-stack-ownership.md) (govern the real systemd/CP-REST/babysitter/alert stack that landed untracked; carries the deferred compose decision)
- ~~★ [platform-doc-coherence-fix](archive/platform-doc-coherence-fix.md)~~ **done & archived** (documentation/ tree gone; live refs re-pointed to TARGET.md/scripts/systemd)
- ★ **[platform-capture-alert-wiring](platform-capture-alert-wiring.md) (P0 — before trusting the unattended week)** — a closed-market/zero-options close and a QC-critical fail both **exit 0 with no alert** today (`qc_fail_alert` defined, no caller; the canary-failure no-op is silent). Wire alerts + a pre-close readiness check. Audit source: 2026-06-15 capture-chain audit.
- ~~★ [platform-rebuild-nonraw-from-raw](archive/platform-rebuild-nonraw-from-raw.md)~~ **core landed & archived 2026-06-17** (`ced031a` — `scripts/rebuild_from_raw.py` guarded purge + replay; deferred tail = QC re-run + signals-layer rebuild on top of the reconstructed non-raw)
- **Archived this pass (2026-06-14):** [platform-ci-pipeline](archive/platform-ci-pipeline.md) **done** (landed as `.github/workflows/gate.yml`, exceeds spec — 3 jobs) · [platform-server-deploy-plumbing](archive/platform-server-deploy-plumbing.md) **superseded** by R4 (CP-REST, not TWS socket); real deploy stack now owned by `platform-deploy-stack-ownership`
- **Landed & archived (2026-06-15):** [platform-data-durability](archive/platform-data-durability.md) **done** — `scripts/backup_data_store.py` (backup/restore/verify) snapshots the keystone (immutable `raw/` + `_run_state.jsonl`; `--include-derived` adds the reconstructable trees) to `$ALGOTRADING_BACKUP_ROOT` as a timestamped, append-only dir with a sha256 manifest; restore lands in a temp store and re-hashes byte-for-byte (refuses canonical without a gate). `data-backup.{service,timer}` + alert fire daily after the close. **Operator decision still required:** point `$ALGOTRADING_BACKUP_ROOT` at a real second location (external disk / NFS / object store) — this box has one physical disk, so a same-disk path is purge/fat-finger protection, not disk-loss. Coordinate with [platform-post-monday-restore-cleanup](archive/platform-post-monday-restore-cleanup.md) (back up the *validated* post-purge state).

**Context hygiene**
- [T-agent-context-minimization](T-agent-context-minimization.md) — Part A (`.agent/` minimum-vital refactor) is partly landed; the `.agent/decisions/` index + glossary trim continue.

## Blocked / parked — do NOT start

- **[1D-futures-term-structure](1D-futures-term-structure.md)** — parked (ADR [0037](../.agent/decisions/0037-futures-capture-deferred-forward-only.md), futures deferred forward-only). The index-only pivot does not re-open it.
- **[T-raw-invariant](infra-raw-invariant.md)** — the ADR-0040 raw-before-derived guard (#1/#2); sequenced after the live-spine wiring it overlaps.
- **REP7 (nautilus-connectivity)** needs a live `TradingNode`; **REP8 (IBKR LST)** needs IBKR live-auth. Specs were retired to git history with the other REP files; revive from history if revisited.
- **[reference-mcp-api-for-llms](reference-mcp-api-for-llms.md)** — expose the BFF API as MCP server(s) for LLM-driven use. **Owner ruled NOT a priority (2026-06-15)**; captured so it is not forgotten. Do not start without a fresh go.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-13 | short intent |`
