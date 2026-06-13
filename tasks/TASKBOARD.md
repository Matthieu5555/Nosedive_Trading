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

## Scope — read first (ADR [0042](../.agent/decisions/0042-index-options-only-scope-ibkr-sole-broker.md))

The app is **index-options-only, EuroStoxx-50-first**. **IBKR is the sole live broker**; **SX5E
is the single live index** (SPX parked `enabled:false`). Single names are index *constituents*,
sourced from the registry — chosen from the enabled index's top-N at the dispersion phase, never
a hand-set option underlying. If any older spec, ADR, or README still says "Saxo", "Deribit",
"three brokers", or "equity underlying", **the index-only pivot wins** — do not resurrect it.

**Ground truth:** one tree (`packages/` + `apps/`). The only gate is
`uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`. Three pytest
failures from the intentional `documentation/` removal are known and left as-is (owner ruling).

## Active claims

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| claude (matthieu) | `tasks/TASKBOARD.md`, `AGENTS.md`; archive 2 landed specs | 2026-06-13 | context-pollution cleanup ([T-agent-context-minimization](T-agent-context-minimization.md) Part A/B) |
| Claude (vincent) | [T-front-currency-and-bands](T-front-currency-and-bands.md) — front display wiring (`api.ts`, `DollarGreeks.tsx`, `MaturityAccordion`, `format.ts`) + un-hardcode `BasketLegGrid` band list | 2026-06-13 | backend `/api/indices` currency single-source already landed; front half remains |
| Claude (anthony) | Basket/Risk tab operator-flow fixes — `routers/basket.py` (empty `trade_date` → latest banked day), web `pages/Basket.tsx`, `pages/RiskScenarios.tsx` | 2026-06-12 | drop the duplicated stress composer from the Risk tab; on-demand stress lives on Basket |

## Ready queue — unclaimed, pick one and claim a row above

Disjoint lanes; anything touching the same file/contract serializes. TARGET §7 is the authority
on order. Each item links its full spec.

**Correctness / capture**
- [T-scenario-rate-axis](T-scenario-rate-axis.md) · [T-strike-window-pct-clip](T-strike-window-pct-clip.md) (latent mine — documented, fix is labelling + delivery test)
- [clock-timer-coherence](clock-timer-coherence.md) (the live SX5E/XEUR timer shift) · [daily-bar-compaction](daily-bar-compaction.md) (971k one-row `daily_bar` files)
- [T-intent-vs-delivery-audit](T-intent-vs-delivery-audit.md) (audit only — hunts the "green gate ≠ correct output" class; findings → tasks)

**Front**
- [T-capture-coverage-panel](T-capture-coverage-panel.md) (BFF + component landed; only the `<CoveragePanel>` drop into `Market.tsx` remains)
- [front-page1-cdc-buildout](front-page1-cdc-buildout.md) (vol scorecards, nappe heatmap, ATM term structure, Greeks-vs-strike cards)

**Phase 2 / Phase 3**
- [2C-pnl-attribution](2C-pnl-attribution.md) · [2D-strategy-composition](2D-strategy-composition.md)
- [3A-order-ticket](3A-order-ticket.md) · [3B-order-sign-and-send](3B-order-sign-and-send.md) (read-only / paper until an explicit owner gate)

**Cross-cutting / config**
- [ci-pipeline](ci-pipeline.md) · [security-review](security-review.md) · [server-deploy-plumbing](server-deploy-plumbing.md)
- [T-pricing-config-completeness](T-pricing-config-completeness.md) (fold the `DEFAULT_MONEYNESS_BUCKETS` literal in here)

**Strategy-book & course-gap lanes (TARGET §7 — ordered there)**
- [T-second-order-greeks](T-second-order-greeks.md) (§7.2 — highest leverage; Vanna/Volga/Charm unblocks attribution + the course 2nd-order req)
- [T-fills-position-store](T-fills-position-store.md) (§7.1 — the book built from fills) · [T-explicit-rate-parameter](T-explicit-rate-parameter.md) (rate as explicit typed config)
- [T-constituent-option-capture](T-constituent-option-capture.md) (§7.4 — S1 dispersion blocker) · [T-signal-layer](T-signal-layer.md) (implied ρ̄ / IV rank / RV−IV / term slope) · [T-delta-hedge-band](T-delta-hedge-band.md)
- [T-rates-curve-ingest](T-rates-curve-ingest.md) (R1) · [T-per-side-surfaces](T-per-side-surfaces.md) (R2 — put/call/combined fit) · [T-mirror-greeks-putcall](T-mirror-greeks-putcall.md) (greeks-only; *not* the same as per-side fit)
- [T-rt-vega](T-rt-vega.md) (#5) · [T-option-volume-capture](T-option-volume-capture.md) (#7) · [T-sigfig-scientific-display](T-sigfig-scientific-display.md) (#6)
- [T-backtester](T-backtester.md) (§7.8) · [T-operational-hardening](T-operational-hardening.md) (§7.9 — margin / kill switch / broker recon / alert delivery)

**Context hygiene**
- [T-agent-context-minimization](T-agent-context-minimization.md) — Part A (`.agent/` minimum-vital refactor) is partly landed; the `.agent/decisions/` index + glossary trim continue.

## Blocked / parked — do NOT start

- **[1D-futures-term-structure](1D-futures-term-structure.md)** — parked (ADR [0037](../.agent/decisions/0037-futures-capture-deferred-forward-only.md), futures deferred forward-only). The index-only pivot does not re-open it.
- **[T-raw-invariant](T-raw-invariant.md)** — the ADR-0040 raw-before-derived guard (#1/#2); sequenced after the live-spine wiring it overlaps.
- **REP7 (nautilus-connectivity)** needs a live `TradingNode`; **REP8 (IBKR LST)** needs IBKR live-auth. Specs were retired to git history with the other REP files; revive from history if revisited.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-13 | short intent |`
