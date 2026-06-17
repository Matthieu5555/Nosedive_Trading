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
> 4. **Risk / stress / scenarios = ONE agent at a time.** ✅ **Resolved 2026-06-17 (Stream B + Onglet-1 lane):**
>    `frontend-scenario-rate-axis-wiring` + `strategy-composition` **landed & archived**, the
>    Onglet-2 ④ Attribution half of `frontend-second-order-greeks-panels` landed (`b2f95bb`), and the
>    **Onglet-1 ③ Panneau Ténor** 2nd-order remainder **landed & archived (`ccef744`)** — the second-order
>    Greeks now render on both panels. The PnL/stress compute substrate was already landed; these were
>    front/BFF wiring slices over it.
> 5. **`frontend-named-scenarios-wiring` is NOT pickable** — its named half landed; only the
>    correlation axis remains and it is **gated/dormant** (no real ρ̄ exposure on the live book —
>    do **not** fabricate one). Blocked until a real `BasketCorrelationExposure` lands.

## Active claims

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| Claude (cockpit-ux) | spec + sequential batches across `apps/frontend/web` + `configs/scenarios.yaml` (see [frontend-cockpit-ux-overhaul](frontend-cockpit-ux-overhaul.md)) | 2026-06-17 | Cockpit UX/visual overhaul (owner-ratified A–E). ① (charts/CSS) + ② (Orders) **landed**. ✅ **Unblocked — `frontend-second-order-greeks-panels` LANDED `ccef744`**: `DollarGreeksByMaturity.tsx` now renders a labelled second-order sub-table off `point.metrics.vanna/volga/charm` (`/api/analytics`). Rebase Batch ③ + E + greeks-table sci-notation onto `ccef744` before touching `DollarGreeksByMaturity.tsx`/`Market.tsx`/`api.ts`. |
| Claude (cockpit-ux) | **RISK/STRESS LANE — reserved**: `pages/Basket.tsx`, `pages/basket/*`, `pages/Ordres.tsx`, `App.tsx`, new `hooks/bookContext.tsx` | 2026-06-17 | ④+⑤ Risk refonte (single-scroll + stress hero) + BookContext carry-through (Risk↔Orders now; Data wired later, Market frozen). **Risk/stress = one agent at a time — do NOT start a concurrent Risk/Basket/stress edit while this is open.** Reorganizes layout only; preserves every b2f95bb feature (compose UI, combined book, 2nd-order attribution, rate sweep). |
| Claude (rebuild-masters-dedup) | `scripts/rebuild_from_raw.py` + `packages/infra/tests/test_rebuild_from_raw.py` | 2026-06-17 | Fix `DuplicateKeyInBatch` on recompute-from-raw: `rebuild_day`'s unscoped `store.read(instrument_master)` returns every captured day's masters (append-only raw), so a key seen on N days yields N snapshot rows at one PK. Dedupe to one master per `instrument_key` (keep max `as_of_date`) via a pure `_distinct_masters` helper + unit test. |

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
- [core-explicit-rate-config](core-explicit-rate-config.md) — **steps 1+2 landed** (step 1: typed `ForwardConfig.rate` home + Eq-5 carry-split override; **step 2 `978a1ee`**: `ForwardCurvePoint` surfaces nullable `implied_rate`/`implied_carry`/`implied_dividend`, `/api/analytics` joins a per-tenor `rate_diagnostics`, additive-nullable). **Still open:** the `r(T)` curve form + the value-changing display default (a flat-rate display that actually moves the carry split).
> **Landed & archived (2026-06-14):** [core-pricing-config-completeness](archive/core-pricing-config-completeness.md) (ADR 0028 — the two deferred slices: `SurfaceConfig.model`/`fallback_model` give the surface-fit method choice a typed home and `fit_slice` reads the labels from config instead of the `METHOD_*` literals — fallback honestly named `nonparametric`, not the blueprint's `spline`; `ForwardConfig.max_candidate_count`/`outlier_method`/`max_robust_zscore` give the forward engine's candidate-cap + outlier policy a typed home in the existing `forward:` block, with `robust.outlier_flags` parameterised by `rejection_z` so the shared util stays decoupled. Zero-churn defaults — None-cap / `mad` / 3.5 = byte-identical fits & forwards; only the `pricing` config-hash moved. `outlier_method: none` and `max_candidate_count` are real opt-in behaviours, tested).
> **Landed & archived (2026-06-14):** [core-projection-moneyness-grid](archive/core-projection-moneyness-grid.md) (ADR 0028 — `SurfaceConfig.moneyness_buckets` gives the surface-projection log-moneyness grid a typed `pricing.yaml` home; the projection chain resolves it from config at the `run_analytics_with_qc` choke and the `DEFAULT_MONEYNESS_BUCKETS` `.py` literal is retired; zero-churn default, only the `pricing` config-hash moved).
> **Landed & archived (2026-06-14):** [core-config-effective-dating](archive/core-config-effective-dating.md) (§0/ADR 0028 — the as-of/effective-dated half: a bundle may date itself with `effective_from`, and `load_platform_config(configs/, as_of=D)` / `from_config(…, as_of=D)` refuse a bundle effective *after* D, so a past-day replay can never silently pick up config authored later; `ProvenanceStamp`/`stamp()` carry the resolved `as_of`, folded into `stamp_hash` only when set. Zero-churn — current path byte-identical, pinned stamp golden unchanged; the additive-nullable `as_of` provenance column regenerated the contracts-plane golden rows. Reach-back to an older in-force version stays the infra profile store's `resolve_as_of`).

**`infra-` — analytics / risk / surface / storage compute**
- ~~[T-capture-tenor-bracket-rewire](archive/T-capture-tenor-bracket-rewire.md)~~ **done & archived 2026-06-17** — capture default is now `max_expiries=None` (keep every listed maturity); nearest-N truncation can no longer silently drop LEAPs on a >64-expiry chain. Delivery test on a 73-expiry chain locks it.
- [infra-rates-curve-ingest](infra-rates-curve-ingest.md) (R1 — **needs an ADR + blueprint amendment first; not pickable yet**) · ~~[infra-mirror-greeks-putcall](archive/infra-mirror-greeks-putcall.md)~~ **done & archived**
- ~~[infra-rt-vega](archive/infra-rt-vega.md)~~ **done & archived** (#5)
- ~~[infra-strike-window-pct-clip](archive/infra-strike-window-pct-clip.md)~~ **done & archived 2026-06-17** — `strike_window_pct` is typed/hashed config; the %-of-spot fallback now fails loud (`StrikeWindowClipError`) when it can't cover the 30Δ band reach, never a silent trim; high-σ/long-tenor delivery test locks it. · ~~[infra-daily-bar-compaction](archive/infra-daily-bar-compaction.md)~~ **done & archived**
- ~~★ [infra-named-scenarios-and-corr-shock](archive/infra-named-scenarios-and-corr-shock.md)~~ **done & archived** (§5.4 — front wiring remainder: [frontend-named-scenarios-wiring](frontend-named-scenarios-wiring.md))
- ★ [infra-tail-risk-var-es](infra-tail-risk-var-es.md) (§5.9 — VaR/ES off the full-reprice distribution + liquidity/concentration; **post-week**, substrate built) · ★ [infra-residual-diagnosis](infra-residual-diagnosis.md) (§7 #10 — regress the attribution residual against unmodeled exposures; **deferred**, gated behind booking + banked realized P&L)
- ★ **[infra-surface-fit-quality](infra-surface-fit-quality.md) (P1 — the "weird nappe" root)** — **LANE 1 LANDED `cc28426`** (+ findings [infra-surface-fit-quality-findings](infra-surface-fit-quality-findings.md)): the dominant `surface_fit_error` failure was a **benign `a_lower` false positive** (`svi_a→0` with positive `w_min`); QC now treats it as benign-but-logged. Settled-close SX5E fail rate **93%→26%**; the served `0.0`-delta axis de-duped at the BFF; ρ-bound KEPT with evidence; ADR-0052 QC recalibration (`f1a6205`) landed earlier. `fit_slice` flag-not-reject stays (blueprint §04.H). **LANE 2 LANDED `c894755` (2026-06-17)** — the Onglet-1 ③ bundle: nappe clamp on the thin ultra-short 10d-wing IV spikes + rate-diagnostics display (`RateDiagnostics.tsx`) + scorecard, gate green (253 vitest). **Still open:** lane 3 (**owner-gated** fallback routing). The residual ultra-short rails are genuine, not a bug.
- **[1D-futures-term-structure](1D-futures-term-structure.md) (GO — owner ruling 2026-06-17)** — pursue listed-futures capture **opportunistically** where IBKR data is obtainable (futures ≡ option-implied forward in information, so the derived forward stays primary/sufficient; this is the captured secondary leg + the forward-vs-futures cross-check the teacher's Tab-1 brief asks for). Supersedes the ADR-0037 forward-only deferral for the *capture* decision. **First step is not code:** land the futures **blueprint amendment + `FuturesPoint` ADR** (blueprint stays the amendable contract, ADR 0011), then build. Off the critical path — 1A→1I ship complete without it.
> **Signal layer (2026-06-14):** [infra-signal-layer](archive/infra-signal-layer.md) **landed** — R3 average
> implied correlation ρ̄ (inverse Eq-23, closed-form) + IV-rank/percentile, RV−IV, term-slope, computed off the
> as-of surfaces/bars/weights and persisted as `strategy_signals` (new `StrategySignal` contract, layer `signals`,
> provider-partitioned). Pure math + as-of orchestrator (`infra/signals/`), blind to alpha. The strategy reads them
> back via `strategy/signal_data.py::signal_snapshot_from_store` — **S1's ρ̄ entry is now live, not fixture-fed**.
> Open follow-ons: daily batch wiring at the `run_analytics` choke; ρ̄ coverage-bias hardening; the realized-corr kill.
> **BFF read surface landed & archived 2026-06-16 (gate green):** [frontend-signals-bff](archive/frontend-signals-bff.md) — `GET /api/signals[/underlyings]` serializes the persisted `strategy_signals` per index and as-of (pydantic-edge house pattern, `strategy_signal_to_dict` + kind→label/unit map, `by_kind` index), recomputing nothing. Consumed by the web Signals page (F-SIG). Open: IV *percentile* persistence (only `iv_rank` is banked today, so it is not surfaced — read-only slice won't recompute it).
> **Landed & archived (2026-06-14, infra-coverage audit):** [infra-pnl-attribution](archive/infra-pnl-attribution.md) (2C by-Greek attribution), [infra-second-order-greeks](archive/infra-second-order-greeks.md) (Vanna/Volga/Charm + Rho/Vanna/Volga + realized day-over-day — compute landed), [infra-scenario-rate-axis](archive/infra-scenario-rate-axis.md) (rate-shock engine+config landed). The front remainders both landed & archived 2026-06-17: [frontend-second-order-greeks-panels](archive/frontend-second-order-greeks-panels.md) (`ccef744`) and [frontend-scenario-rate-axis-wiring](archive/frontend-scenario-rate-axis-wiring.md).
> **R2 per-side surfaces (2026-06-14):** [infra-per-side-surfaces](archive/infra-per-side-surfaces.md) **infra core landed** (ADR 0048 — per-side fit, `surface_side` grid PK, put−call IV spread signal + QC), then [infra-basket-leg-per-side-routing](archive/infra-basket-leg-per-side-routing.md) **landed** — `BasketLeg.surface_side` opt-in routes the summed basket + BFF reprice to each leg's wing (booking stays combined). Front remainder ~~[frontend-per-side-surfaces-toggle]~~ **retired 2026-06-17** (see ready queue — superseded by the side-agnostic smile overlay).

**`ibkr-` — IBKR capture lane & connectivity**
- ~~[EMERGENCY-quote-integrity-gate](archive/EMERGENCY-quote-integrity-gate.md)~~ **done & archived** — refuse to bank a last-only / market-closed capture; quarantine single-sided/zero-spread rows; enforce `completeness`/`flags`. **Quote integrity stays real under ADR 0051.**
- ~~[EMERGENCY-constituent-lane-activation](archive/EMERGENCY-constituent-lane-activation.md)~~ **retired (ADR 0051)** — the constituent-option lane it activated is gone; constituents are prices-only. Done-then-mooted.
- ~~[EMERGENCY-capture-throughput](archive/EMERGENCY-capture-throughput.md)~~ **dissolved (ADR 0051)** — the throughput emergency was an artifact of constituent-option capture, which no longer happens; the index's own chain is already in-window. Its three follow-ups (cross-underlying / snapshot-warmup / intraday-conid) are all retired below.
- ~~[ibkr-capture-cross-underlying-concurrency](archive/ibkr-capture-cross-underlying-concurrency.md)~~ **landed 2026-06-16 (`b128d6b`), now moot (ADR 0051)** — fan-out under one shared gateway budget; value remains only for the single index chain.
- ~~[ibkr-snapshot-warmup-concurrency](archive/ibkr-snapshot-warmup-concurrency.md)~~ **retired & archived 2026-06-16 (ADR 0051)** — constituent-walk throughput optimization; moot for index-only capture. Revive only if a future pure-implied dispersion re-opens single-name capture.
- ~~[ibkr-intraday-conid-cache](archive/ibkr-intraday-conid-cache.md)~~ **retired & archived 2026-06-16 (ADR 0051)** — same constituent-walk lane; moot for index-only capture.
- ~~[ibkr-option-volume-capture](archive/ibkr-option-volume-capture.md) (#7)~~ **done & archived** (branch `ibkr-option-volume-capture`, 2026-06-15)
- ~~[ibkr-clock-timer-coherence](archive/ibkr-clock-timer-coherence.md)~~ **done & archived** (guard hotfix live; XEUR timer 18:15→22:45 Berlin; drift test)
- ★ [ibkr-unattended-reauth](ibkr-unattended-reauth.md) (§5.9 — close the ~daily SMS-2FA wall) — **code-side DONE 2026-06-17 (Stream D, `6ae3216`):** SSO-expiry ALARM now delivered through the C4 alert seam (pushed, not log-only) + manual SMS-relogin runbook (`scripts/systemd/RUNBOOK-reauth.md`, dedicated 2nd username + pre-close verify). **⛔ OPEN = OWNER ACTION:** the no-SMS OAuth 1.0a path is blocked on IBKR Self-Service "Enable OAuth Access → 400 not authenticated" enrollment — until the owner clears it the week runs attended-with-alerts, not unattended. Secondary tail: wire the real two-sided-fraction pre-close probe (`preclose_readiness.probe_two_sided_fraction` is a conservative stub).
- ~~★ [ibkr-broker-account-read](archive/ibkr-broker-account-read.md)~~ **done & archived** (§5.9/§6 — read-only CP-REST positions/cash/fills; feeds the recon sub-lane of [execution-operational-hardening](archive/execution-operational-hardening.md))

**`strategy-` — the strategy book, signals, backtester** (`packages/strategy` spine **landed** — [strategy-contract-base](archive/strategy-contract-base.md): typed contract + protocol + 4-context harness + `strategy_id` stamp; the S-specs and backtester build on it.)
- ~~★ [strategy-s4-covered-strangle](archive/strategy-s4-covered-strangle.md)~~ **done & archived** (§3 S4) · ~~★ [strategy-s5-calendar-carry](strategy-s5-calendar-carry.md)~~ **done `f20aa2b`** (§3 S5, optional)
- ~~[strategy-composition](archive/strategy-composition.md)~~ **done & archived 2026-06-17 (Stream B)** — the BFF compose/book router (`routers/compose.py`: `GET /api/compose/sub-strategies` + `POST /api/compose` → combined Greeks + per-layer + combined stressed PnL surface, `e9e19e5`), the web compose + combined-view UI (Onglet 2 ① Composer + ② Le book, `b2f95bb`), and the two missing tests (`test_book_config_hash_cross_process`, `test_diversification_diagnostic_is_read_only`) + readback seam landed; gate green. The §5.8 **correlation-view / decorrelation tail** is the separate post-week [strategy-decorrelation-analytics](strategy-decorrelation-analytics.md).
- ~~[strategy-backtester](archive/strategy-backtester.md)~~ **landed & archived 2026-06-16** (§7.8 — research machine + prod-shadow / store adapter / txn-cost / `POST /api/backtest/run`, gate green 2384; **deferred tail = P&L-level shadow**, revive a stub when picked up) · ~~[strategy-decorrelation-analytics](archive/strategy-decorrelation-analytics.md)~~ **done & archived 2026-06-17 (`77ebc31`+`e0e8b1c`)** — §5.8 decorrelation *verification* over the landed 2D book: a pure `infra/risk/decorrelation.py` surfaces read-only cross-strategy **stressed-P&L correlation** + **shared-tail overlap** (the S1/S3 low-vol shared-failure-mode is **visibly detected** in `test_shared_failure_mode_is_visibly_detected`) + **factor overlap** (Greek-vector cosine) + **marginal contribution to risk** (leave-one-out on the book's worst stressed loss), serialized as an additive `decorrelation` block on `/api/compose`. **Realized-series cross-correlation + marginal Sharpe are gated honestly** — no banked per-layer realized P&L on the live book, no fabricated ρ̄ (reason strings, never an invented matrix). Read-only, no optimiser (`test_no_optimiser_present`), no `apps/frontend/web`. Gate green (2843 passed, 12 skipped; module 100% branch cov).

> **Landed & archived (2026-06-14):** [strategy-s1-dispersion](archive/strategy-s1-dispersion.md) **done** — the flagship S1 dispersion object (`DispersionStrategy` + store-backed `StoreBackedDispersionData`): ρ̄-rich entry, point-in-time top-N straddles routed put/call (the first ADR-0048 per-side consumer), a delta-flattening synthetic short-forward index leg, net-vega-collapse kill. v1 forward-only; v2 (short index straddle) deferred. The ρ̄ source ([infra-signal-layer](archive/infra-signal-layer.md)) **landed** — S1's entry now reads the persisted signal layer (live, not fixture-fed). v2 (short index straddle) remains the open lane upgrade.
>
> **Landed & archived (2026-06-15):** [strategy-s2-index-put-line](archive/strategy-s2-index-put-line.md) **done** — the
> S2 index short-put line (`PutLineStrategy`, config-only — no data adapter): the deliberate opposite tail to S1. A
> rolling line that sells one ~25Δ ~30d index put/day, gated by `decide_sell` = the RV−IV signal (implied rich vs
> realized) **and** a config capacity cap (course's 30-open rolling line); the put routes to the put wing (ADR 0048) at
> the **steered** `put_delta_band` (the assignment-frequency lever). `decide_exit` flattens on a net-delta drawdown
> proxy and otherwise **defers to the execution kill switch** ([execution-operational-hardening](archive/execution-operational-hardening.md),
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
- [execution-order-sign-and-send](execution-order-sign-and-send.md) — **3B paper path LANDED 2026-06-17 (C6, `5c63a61`)**: `SignedTicket`/`TransmissionDecision`/binding-hash + fail-closed `decide_transmission` + append-only transmit audit + the separate IBKR `CpRestOrderSubmit` leaf verb (never on the read-only ingestion transport, ADR 0024 §4 still asserted); full 24-cell decision table tested; flag-absent never invokes submit. **Live transmit stays OFF** (`EXECUTION_TRANSMIT_ENABLED` absent → `BLOCKED_DEFAULT`). Remaining = a *future* live increment only: owner flag `live` + valid ticket-bound sign-off + recorded-green `EXECUTION_SECURITY_REVIEW` (the §2 review below is now green).
- ~~[execution-operational-hardening](archive/execution-operational-hardening.md)~~ **DONE & ARCHIVED 2026-06-17** (§7.9 umbrella, all four sub-lanes landed): margin/InvWC capacity (C3, `b231924`), kill switch (C2, `11006d3`), broker recon (prior, `f72c1e1`/`7238ef8`), alert delivery (C4, `d944b41` — the shared `AlertSink` seam).

**`frontend-` — BFF + web delivery (apps/frontend)**
- ✅ **Landed & archived 2026-06-17:** [frontend-3onglets-consolidation](archive/frontend-3onglets-consolidation.md) — the **shell flip** landed on top of the fleet's Onglet-0/1/2/3 content (`8c73d56`/`d918dbc`/`b2f95bb`/`5d8eb5b`): `routes.ts`/`App.tsx` now render **exactly 3 onglets** (Données / Risque / Ordres); Market→Données, Basket→Risque, Ordres wired; Operations demoted to a secondary topbar utility; Signals dropped; legacy paths redirect; the folded standalone pages (RiskScenarios/Positions/Strategy/Signals + SignalsView) retired. Full web gate green (tsc + lint + 218 vitest + 31 Playwright e2e). The BFF prerequisite [frontend-bff-bidask-volume](archive/frontend-bff-bidask-volume.md) (per-option bid/ask+volume on `/api/analytics`) landed on main as `8c73d56` (the `quote` block) — archived as done.
- ✓ **Landed & archived 2026-06-16/17 (the page-1 cluster is settled — do NOT reopen):** [frontend-page1-reading-model](archive/frontend-page1-reading-model.md) (`c4ce734` — the owner-locked scrollable reading model: price → scorecards → 3D nappe → one tenor selector driving put/call smile + greeks → ρ̄) **supersedes** ~~[frontend-page1-cdc-buildout](archive/frontend-page1-cdc-buildout.md)~~ (heatmap + accordion dropped) and subsumes ~~[frontend-page1-cdc-0051-correction](archive/frontend-page1-cdc-0051-correction.md)~~ (`6605990` — the `c665614` rebuild misframing is corrected, not preserved). ~~[frontend-tab-shell](archive/frontend-tab-shell.md)~~ + ~~[frontend-signals-bff](archive/frontend-signals-bff.md)~~ also landed (gate green). **Open follow-on:** surface-fit front robustness (lane 2 of [infra-surface-fit-quality](infra-surface-fit-quality.md)) re-homes onto the landed reading model.
- ~~★ [frontend-second-order-greeks-panels](archive/frontend-second-order-greeks-panels.md)~~ **DONE & ARCHIVED 2026-06-17 (`ccef744`)** — the Onglet-1 ③ remainder landed. **Measured correction:** the `/api/risk/metrics` (`pricing_results`) layer is **empty per index close**, so the spec's read-from-`RiskMetricCell` premise was dead; and the second-order math was **already computed** by `price_european` at projection time and silently dropped. So the fix was a no-new-math carry-through on the analytics grid: `ProjectedOptionAnalytics` gained additive-nullable vanna/volga/charm (+$+unit), projection now passes the computed values through `dollar_greeks`, the BFF analytics serializer surfaces `metrics.vanna/volga/charm`, and `DollarGreeksByMaturity.tsx` renders a labelled second-order sub-table (explicit gap note for pre-field closes). Independent finite-difference cross-check locks vanna=∂Δ/∂σ, volga=∂vega/∂σ, charm=−∂Δ/∂T. Both 2nd-order panels now reach the screen (Onglet-2 ④ Attribution `b2f95bb` earlier; Onglet-1 ③ now). · ~~[frontend-scenario-rate-axis-wiring](archive/frontend-scenario-rate-axis-wiring.md)~~ **done & archived 2026-06-17 (Stream B)** — the on-demand **Basket** rate sweep on `/api/basket/scenarios` (`a40224f`) + the `StressTab` `RateSweep` render (`b2f95bb`) landed; the persisted `/api/risk/scenarios` rate path landed earlier. Gate green.
- [frontend-capture-coverage-panel](frontend-capture-coverage-panel.md) — **v1 LANDED & MOUNTED** on Onglet 1 (`d918dbc`; `/api/coverage` + `CoveragePanel` at `Market.tsx:224`, no-look-ahead read test `fe7cfed`). **Still open (phase 2):** the quote-completeness add (per-expiry bid/ask/volume coverage beyond strike/tenor counts) — genuine residual, kept open.
- ★ **MAT-LEGIBILITY cluster (owner ask 2026-06-17, "the user must know wtf is going on")** — surface, on Onglet 1, how much of the captured chain the strict surface actually rests on, why rows were excluded, and let the PM pick strict vs indicative without ever confusing them. Three disjoint specs, land in order; share one coverage/two-sided-fraction contract (do **not** fork the metric with `frontend-capture-coverage-panel` phase-2). All read-only / additive except #3.
  - [MAT-LEGIBILITY-coverage-headline](MAT-LEGIBILITY-coverage-headline.md) — **first, cheapest.** Always-visible "Nappe sur X / Y cotations · Z exclues" headline under the 3D nappe, sourced from `qc_results` `two_sided_option_count`/`option_leg_count` (already on disk); quiet/partial/degenerate states off the `QcBadge` palette. Additive `coverage` block on `/api/analytics`.
  - [MAT-LEGIBILITY-quarantine-drilldown](MAT-LEGIBILITY-quarantine-drilldown.md) — disclosure off the headline: *why* rows were excluded (by reason `missing_side`/`non_positive_bid`/`non_positive_ask`/`crossed` + by tenor), re-derived read-only from `raw_market_events` via the **same** capture predicate (`_two_sided_quote_reason`, extracted to a shared helper — the one real refactor). Total reconciles with the headline.
  - [MAT-LEGIBILITY-strict-indicative-mode](MAT-LEGIBILITY-strict-indicative-mode.md) — **biggest, last; net-new engine compute, not a flag flip.** Strict⟷Indicative toggle on Onglet 1 with an unmissable "INDICATIF" badge + per-point provenance. **⛔ Hard guardrail: strict stays the default/canonical/stored surface; indicative is a non-persisted view-time overlay that never reaches the risk/strategy path.** **One open owner/quant decision first (no code): what indicative actually prices** (one-sided mark vs `last` vs both). Spans infra (`driver.py`)+BFF+front — split per-lane, serialize the Market.tsx slice behind the live front lane.
- ~~★ [frontend-per-side-surfaces-toggle](archive/frontend-per-side-surfaces-toggle.md)~~ **retired & archived 2026-06-17** — the put/call *toggle* was superseded by the shipped side-agnostic smile overlay (`charts.tsx:200-205` — "the page no longer has a put/call switch; the asymmetry is the point"). Infra core (ADR 0048) stays landed; if the raw per-side / IV-spread *payload* is ever wanted, open a fresh narrower BFF spec — do not resurrect the toggle.
- ✓ **Landed 2026-06-14 (core-fleet):** [frontend-attribution-view](archive/frontend-attribution-view.md) (§7 #2 — BFF router + attribution waterfall over `ScenarioAttribution`, wired on Basket) · [frontend-orders-booking-reconcile](archive/frontend-orders-booking-reconcile.md) (§7 #1 coherence — dead `Orders.tsx` retired, `/orders` redirects to the one booking home on Basket)
- ✓ **Landed 2026-06-15, archived:** [frontend-sigfig-scientific-display](archive/frontend-sigfig-scientific-display.md) (#6 — sci-notation @ 6 sig-figs + currency landed via the sci-notation + `T-front-currency-and-bands` passes; `lib/format.ts` `sci`/`sciUnit` is the single home). *(Stale active duplicates of the two attribution/orders specs were also removed this pass — the archived copies are canonical.)*

**`platform-` — CI/CD, deploy, security, ops & audits (cross-cutting; not a package)**
- ★ **[T-clean-ingestion-2026-06-16](T-clean-ingestion-2026-06-16.md) (P2 ops — from the 2026-06-17 ingestion audit)** — re-derive 2026-06-16 from raw (post-0052 QC, the `qc=fail` was pre-0052) + re-run QC + prune the 42 stale `run=` partitions. **Blueprint-conform: the degenerate ultra-short slice STAYS (flag-not-reject); it is NOT removed here** — its front clamp is [infra-surface-fit-quality](infra-surface-fit-quality.md) lane 2. Recompute-from-raw, reversible, nothing lost. **Step 0 = provisional archive of everything altered (`data/_provisional_archive/2026-06-16-pre-cleanup/`) before any mutation; REQUIRED post-validation cleanup deletes it after owner sign-off** (don't leave stale shadow data). *(Ingestion-audit map: F1 capture-nearest-N → [T-capture-tenor-bracket-rewire](T-capture-tenor-bracket-rewire.md); F3 ultra-short noise → infra-surface-fit-quality lane 2 front clamp; F2 long-end floors → ADR 0052 / [archive](archive/infra-qc-coverage-to-blueprint.md), in WARNING.)*
- ~~[platform-security-review](archive/platform-security-review.md)~~ **COMPLETE 2026-06-17 → [platform-security-review-2026-06-17.md](platform-security-review-2026-06-17.md)** (the refreshed full verdict). All five sections done; **M2 CLOSED** (C5, `b1230f3` — booking audit now write-ahead of the fill write, `commit.py:215` before `:216`, regression-pinned); **§2 order-seam reviewed GREEN** against the landed 3B paper seam (C-tech-lead, `377adee`). **No CRITICAL/HIGH.** Advisory only: M1 (starlette 1.2.1 CVE — BFF dep bump), M3 (`verify_tls` default), L1/L2/L3. Nothing blocks paper operation; a future live flip is gated solely on the owner action (flag + recorded-green handshake, both now satisfiable).
- ~~[platform-intent-vs-delivery-audit](archive/platform-intent-vs-delivery-audit.md)~~ **done & archived 2026-06-16** — findings in [T-intent-vs-delivery-audit-findings-2026-06-16](T-intent-vs-delivery-audit-findings-2026-06-16.md). 7 confirmed (F1 latent nearest-N regression `38910d9` → now [T-capture-tenor-bracket-rewire](T-capture-tenor-bracket-rewire.md); F2 long-end QC floor; F3 surface_fit measured_value; F4 forward cap; F5 scenario clip; F6 ADR-0028 literals; F7 storage re-capture visibility) + 2 overturned false positives. F2–F7 tracked in the findings doc (no separate files). · ~~[platform-post-monday-restore-cleanup](archive/platform-post-monday-restore-cleanup.md)~~ **done & archived 2026-06-17** (5 synthetic 06-12 ledger rows purged; backup kept)
- ~~★ [platform-secret-and-dep-scan](archive/platform-secret-and-dep-scan.md)~~ **done & archived** · ~~★ [platform-deploy-stack-ownership](archive/platform-deploy-stack-ownership.md)~~ **done & archived 2026-06-17 (Stream D, `cb790bc`)** — `scripts/systemd/README.md` operating contract (units/cadence, 3 session clocks, exit codes, per-alarm action), ADR 0055 records compose **dropped** (systemd is the deploy), importable `scripts/eod_healthcheck.py` smoke-path, dead deploy-doc refs repointed.
- ~~★ [platform-doc-coherence-fix](archive/platform-doc-coherence-fix.md)~~ **done & archived** (documentation/ tree gone; live refs re-pointed to TARGET.md/scripts/systemd)
- ~~★ **[platform-capture-alert-wiring](archive/platform-capture-alert-wiring.md) (P0)**~~ **done & archived 2026-06-17 (Stream D, `37ab66c`)** — both remaining items closed: (1) alerts now DELIVER by consuming Stream-C's landed C4 `AlertSink` seam (no fork) + a new `degenerate_close_alert` so a closed-market/zero-options close PAGEs and exits non-zero (never a silent green); (2) a pre-close readiness check (`preclose_readiness.py`). Integration tests lock "degenerate close ⇒ alert delivered AND non-zero exit" (`b981836`). Tail: the two-sided-fraction probe is a conservative stub (tracked on [ibkr-unattended-reauth](ibkr-unattended-reauth.md)).
- ~~★ [platform-rebuild-nonraw-from-raw](archive/platform-rebuild-nonraw-from-raw.md)~~ **core landed & archived 2026-06-17** (`ced031a` — `scripts/rebuild_from_raw.py` guarded purge + replay; deferred tail = QC re-run + signals-layer rebuild on top of the reconstructed non-raw)
- **Archived this pass (2026-06-14):** [platform-ci-pipeline](archive/platform-ci-pipeline.md) **done** (landed as `.github/workflows/gate.yml`, exceeds spec — 3 jobs) · [platform-server-deploy-plumbing](archive/platform-server-deploy-plumbing.md) **superseded** by R4 (CP-REST, not TWS socket); real deploy stack now owned by `platform-deploy-stack-ownership`
- **Landed & archived (2026-06-15):** [platform-data-durability](archive/platform-data-durability.md) **done** — `scripts/backup_data_store.py` (backup/restore/verify) snapshots the keystone (immutable `raw/` + `_run_state.jsonl`; `--include-derived` adds the reconstructable trees) to `$ALGOTRADING_BACKUP_ROOT` as a timestamped, append-only dir with a sha256 manifest; restore lands in a temp store and re-hashes byte-for-byte (refuses canonical without a gate). `data-backup.{service,timer}` + alert fire daily after the close. **Operator decision still required:** point `$ALGOTRADING_BACKUP_ROOT` at a real second location (external disk / NFS / object store) — this box has one physical disk, so a same-disk path is purge/fat-finger protection, not disk-loss. Coordinate with [platform-post-monday-restore-cleanup](archive/platform-post-monday-restore-cleanup.md) (back up the *validated* post-purge state).

**Context hygiene**
- [T-agent-context-minimization](T-agent-context-minimization.md) — **Part A DONE; Part-B #1 DONE 2026-06-17 (Stream D, `01ab09e`):** the `provider="DERIBIT"` code default flipped to `"IBKR"` (`normalize.py:22`, `events.py:29` + golden fixtures), closing intent-vs-delivery F6 + ingestion-audit #14. **Only open item: Part-B #4** — encode the ADR-0051 universe-model rule (index options + constituent *prices only*) in ADR 0035/0042. Low-priority ADR-lane chore.

## Blocked / parked — do NOT start

- **[T-raw-invariant](infra-raw-invariant.md)** — the ADR-0040 raw-before-derived guard (#1/#2); sequenced after the live-spine wiring it overlaps.
- **REP7 (nautilus-connectivity)** needs a live `TradingNode`; **REP8 (IBKR LST)** needs IBKR live-auth. Specs were retired to git history with the other REP files; revive from history if revisited.
- **[reference-mcp-api-for-llms](reference-mcp-api-for-llms.md)** — expose the BFF API as MCP server(s) for LLM-driven use. **Owner ruled NOT a priority (2026-06-15)**; captured so it is not forgotten. Do not start without a fresh go.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-13 | short intent |`
