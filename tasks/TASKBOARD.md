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
it. The gate (the only one) is in `AGENTS.md`; **green** 2026-06-13 (1507 passed, 12 skipped).

## Active claims

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| claude (matthieu) | `tasks/TASKBOARD.md`, `AGENTS.md`; archive 2 landed specs | 2026-06-13 | context-pollution cleanup ([T-agent-context-minimization](T-agent-context-minimization.md) Part A/B) |
| Claude (vincent) | [T-front-currency-and-bands](T-front-currency-and-bands.md) — front display wiring (`api.ts`, `DollarGreeks.tsx`, `MaturityAccordion`, `format.ts`) + un-hardcode `BasketLegGrid` band list | 2026-06-13 | backend `/api/indices` currency single-source already landed; front half remains |
| Claude (anthony) | Basket/Risk tab operator-flow fixes — `routers/basket.py` (empty `trade_date` → latest banked day), web `pages/Basket.tsx`, `pages/RiskScenarios.tsx` | 2026-06-12 | drop the duplicated stress composer from the Risk tab; on-demand stress lives on Basket |
| claude (matthieu) | `packages/execution/**` (new `fills`/`ledger`/`book`), `pyproject.toml` testpaths | 2026-06-14 | [execution-fills-position-store](execution-fills-position-store.md) **store + readers landed** (branch `execution-fills-position-store`); the remaining half is the gated write path [execution-booking-commit](execution-booking-commit.md) |

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

## Ready queue — unclaimed, pick one and claim a row above

Disjoint lanes; anything touching the same file/contract serializes. TARGET §7 is the authority
on order. Grouped by owning layer; each item links its full spec. **★ = new spec from the
planning pass.**

**`core-` — config & lineage spine (level 0)**
- [core-explicit-rate-config](core-explicit-rate-config.md) — **step 1 landed** (typed `ForwardConfig.rate` home + Eq-5 carry-split override, zero-churn `null` default; open = `forward_curve` contract/display, `r(T)` curve; the compute-wiring slice is infra's)
- [core-pricing-config-completeness](core-pricing-config-completeness.md) (fold the `DEFAULT_MONEYNESS_BUCKETS` / surface-model / forward-policy literals into typed `pricing.yaml`)
- ★ [core-config-effective-dating](core-config-effective-dating.md) (§0/ADR 0028 — the unbuilt as-of/effective-dated half of config; a real look-ahead hole — replay of an old `as_of` silently resolves *today's* config)

**`infra-` — analytics / risk / surface / storage compute**
- [infra-second-order-greeks](infra-second-order-greeks.md) (§7.2) — **steps 1-2 (compute) landed** (Vanna/Volga/Charm raw+cash+units; attribution carries Rho/Vanna/Volga + realized day-over-day). Step 3 (front) is now [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md).
- [infra-pnl-attribution](infra-pnl-attribution.md) (§5.2 engine) · [infra-scenario-rate-axis](infra-scenario-rate-axis.md) (§5.4 — **engine+config landed**; BFF/front slice is [frontend-scenario-rate-axis-wiring](frontend-scenario-rate-axis-wiring.md))
- [infra-rates-curve-ingest](infra-rates-curve-ingest.md) (R1) · [infra-per-side-surfaces](infra-per-side-surfaces.md) (R2 — put/call/combined fit) · [infra-mirror-greeks-putcall](infra-mirror-greeks-putcall.md) (greeks-only; *not* the per-side fit)
- [infra-signal-layer](infra-signal-layer.md) (implied ρ̄ / IV rank / RV−IV / term slope; consumes [ibkr-constituent-option-capture](ibkr-constituent-option-capture.md)) · [infra-rt-vega](infra-rt-vega.md) (#5)
- [infra-strike-window-pct-clip](infra-strike-window-pct-clip.md) (latent mine — labelling + delivery test) · [infra-daily-bar-compaction](infra-daily-bar-compaction.md) (971k one-row `daily_bar` files)
- ★ [infra-named-scenarios-and-corr-shock](infra-named-scenarios-and-corr-shock.md) (§5.4 — named historical stress 2008/COVID + correlation-shock axis; reuses the 2B grid + landed rate-axis pattern)

**`ibkr-` — IBKR capture lane & connectivity**
- [ibkr-constituent-option-capture](ibkr-constituent-option-capture.md) (§7.4 — S1 dispersion blocker) · [ibkr-option-volume-capture](ibkr-option-volume-capture.md) (#7)
- [ibkr-clock-timer-coherence](ibkr-clock-timer-coherence.md) (the live SX5E/XEUR timer shift)
- ★ [ibkr-unattended-reauth](ibkr-unattended-reauth.md) (§5.9 — close the ~daily SMS-2FA wall; OAuth bring-up + SSO-expiry ALARM delivery. **Load-bearing for the unattended-week story**)

**`strategy-` — the strategy book, signals, backtester**
- ★ [strategy-s1-dispersion](strategy-s1-dispersion.md) (§3 S1 — flagship, week goal; blocked on `ibkr-constituent-option-capture`) · ★ [strategy-s2-index-put-line](strategy-s2-index-put-line.md) (§3 S2) · ★ [strategy-s3-gamma-trading](strategy-s3-gamma-trading.md) (§3 S3)
- ★ [strategy-s4-covered-strangle](strategy-s4-covered-strangle.md) (§3 S4) · ★ [strategy-s5-calendar-carry](strategy-s5-calendar-carry.md) (§3 S5, optional)
- [strategy-delta-hedge-band](strategy-delta-hedge-band.md) (hedge rule for S1/S3/S4) · [strategy-backtester](strategy-backtester.md) (§7.8) · ★ [strategy-decorrelation-analytics](strategy-decorrelation-analytics.md) (§5.8 — decorrelation *verification*, post-week; depends on 2D)

**`execution-` — OMS / booking chain (packages/execution, empty)**
- ★ [execution-booking-commit](execution-booking-commit.md) — **§7 #1, week's top priority**: the password-gated booking write barrier (previewed ticket → paper fill → fills-store + audit)
- [execution-order-ticket](execution-order-ticket.md) · [execution-order-sign-and-send](execution-order-sign-and-send.md) (read-only / paper until an explicit owner gate) · [execution-fills-position-store](execution-fills-position-store.md) (§7.1 — **store + readers landed**: `Fill` + append-only `FillsLedger` (in-mem + durable JSONL) + `position_set_from_fills` folding into the `PositionSet` risk already reads; the gated *write* path is `execution-booking-commit`)
- [execution-operational-hardening](execution-operational-hardening.md) (§7.9 umbrella — margin / kill switch / broker recon / alert delivery; margin sub-lane gates S2, rest post-week)

**`frontend-` — BFF + web delivery (apps/frontend)**
- [frontend-page1-cdc-buildout](frontend-page1-cdc-buildout.md) (vol scorecards, nappe heatmap, ATM term structure, Greeks-vs-strike cards) · [frontend-sigfig-scientific-display](frontend-sigfig-scientific-display.md) (#6)
- ★ [frontend-coverage-panel-drop](frontend-coverage-panel-drop.md) (drop the landed `<CoverageTable>` into `Market.tsx` — supersedes the open slice of [T-capture-coverage-panel](T-capture-coverage-panel.md)) · ★ [frontend-second-order-greeks-panels](frontend-second-order-greeks-panels.md) (step 3 of infra-second-order-greeks; after 3A + sigfig) · ★ [frontend-scenario-rate-axis-wiring](frontend-scenario-rate-axis-wiring.md) (BFF/front slice of infra-scenario-rate-axis)

**Cross-cutting / unowned (no single layer; pick up directly)**
- [2D-strategy-composition](2D-strategy-composition.md) (Phase 2 — infra/risk + BFF + web vertical, left whole) · [T-intent-vs-delivery-audit](T-intent-vs-delivery-audit.md) (all-layers audit — "green gate ≠ correct output"; findings → tasks)
- [ci-pipeline](ci-pipeline.md) · [security-review](security-review.md) · [server-deploy-plumbing](server-deploy-plumbing.md) (front-app slices fold into these, not split out)

**Context hygiene**
- [T-agent-context-minimization](T-agent-context-minimization.md) — Part A (`.agent/` minimum-vital refactor) is partly landed; the `.agent/decisions/` index + glossary trim continue.

## Blocked / parked — do NOT start

- **[1D-futures-term-structure](1D-futures-term-structure.md)** — parked (ADR [0037](../.agent/decisions/0037-futures-capture-deferred-forward-only.md), futures deferred forward-only). The index-only pivot does not re-open it.
- **[T-raw-invariant](infra-raw-invariant.md)** — the ADR-0040 raw-before-derived guard (#1/#2); sequenced after the live-spine wiring it overlaps.
- **REP7 (nautilus-connectivity)** needs a live `TradingNode`; **REP8 (IBKR LST)** needs IBKR live-auth. Specs were retired to git history with the other REP files; revive from history if revisited.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-13 | short intent |`
