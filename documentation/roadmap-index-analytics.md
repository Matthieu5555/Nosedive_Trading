# Roadmap — the index options-analytics pipeline

This is a **how-to / planning** doc: it turns the owner's spoken brief (the prof's two
course recordings, transcribed in [`transcripts/`](transcripts/)) plus the
[`vision-medium-term.md`](vision-medium-term.md) target into a sequenced, buildable plan.
It is the artefact the vision doc promised — *"detailed task specs follow once the owner's
brief lands."* The brief has landed; this is the bridge from it to `tasks/`.

**Authority chain.** The blueprint (`documentation/blueprint/`, ADR 0011) remains the domain
contract and **overrides this doc** on any formula, field, tenor, or definition. Where the
brief asks for something the blueprint does not yet cover (notably **futures capture**), this
doc flags it as needing a blueprint amendment + ADR before build — it does not silently
invent contract.

**Sources behind every line here.** Two transcripts (the data brief + the front-end review),
the medium-term vision, the blueprint, and a quant-conventions research pass (Q1–Q4 in
§2). Where a decision rests on outside convention, the source is named inline.

---

## 0. Scope, as decided

- **Both tabs, sequenced.** Tab 1 (the data foundation) is built in full first — the prof's
  framing: *"la première partie qui semble la moins sexy… si vous avez ces données, vous avez
  tout."* Tab 2 (risk + strategy) follows. **Order execution** (ticket → signed email → send)
  is **sketched only** — the prof himself calls it *"la partie la plus étrange… pas grave"*
  for now.
- **Universe to prove on:** **EURO STOXX 50** first (the prof's *"bon équilibre"*), with
  **S&P 500** as the stretch target (~504 names, ~5y history, heavier). This choice drives the
  data-source decision (§2, OQ-2) toward a **European** options-history vendor.

```text
  Tab 1 — DATA (agnostic foundation)            Tab 2 — RISK & STRATEGY        Exec (sketch)
  index → constituents → chain                  basket builder                 ticket
  futures TS · options (Δ-band)                 stress ±50% spot / ±50% vol    signed email
  IV · surface · Greeks ($+decimal)             PnL attribution by Greek       send orders
  daily close snapshots (cron) · QC             decorrelated-strategy view
  front: pick ticker → max history
```

---

## 1. What the brief adds to the vision (decoded from the transcripts)

The transcripts are noisy auto-transcriptions; this is the reconciled signal, confirmed with
the owner.

### Tab 1 — the data foundation ("agnostic", indispensable)
- Start from an **index**, resolve its **full constituent list**, then for each name the
  **option chain**.
- **Futures term structure** per tenor on the index (and where listed, constituents):
  **10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y** (pinned grid — §2, OQ-4).
- **Options** puts & calls across the **delta band**: every listed strike from the **30Δ put,
  through ATM, to the 30Δ call** (not three pillars — the whole central smile).
- Per option: **price, implied vol, fitted vol surface, all Greeks (Δ, Γ, Vega, Θ, Rho)**, in
  **two representations side by side — decimal and dollar**.
- **Daily close-price snapshots**, captured by a **cron** so history accrues unattended;
  operator can pull any **historical window (1 month … 5 years)**.
- **Underlying daily price history** for the **index and *every* constituent** — a distinct
  product from the option snapshots, captured via **IBKR historical bars**, powering the
  **ticker chart** shown beside the scrollable constituent list. Capture full **OHLC** per day
  (not just close) so a **candlestick** chart is available at no extra cost.
- UI grammar: an **accordion per maturity**; a **smile graph (vol vs delta) per maturity**.

### Tab 2 — risk & strategy
- **Basket builder**: select stocks + options into a position (e.g. ATM straddle on the first
  names), ergonomically.
- **Stress / scenario**: shock **spot ±50 %** and **vol ±50 %** on a grid; watch the **PnL
  surface** move.
- **PnL attribution by the Greeks** (dPnL decomposed into delta / gamma / vega / theta
  contributions).
- Compose **small, decorrelated strategies** into a combined book.

### Execution (sketch, not prioritised)
- Build a **ticket**, **sign via email**, **send orders**. Read-only until explicitly gated.

### Front-end (from the front-end review transcript)
- **3D vol surface** (replace the unreadable 2D view) for direct intuition.
- **Underlying price chart** beneath — **candlestick preferred over a line** if not too heavy
  (a *want*, not a hard requirement; the rendering choice is deferred); everything **scrollable**.
- **Dollar Greeks** shown.
- **Logical order**: price first, then descend into detail. *"Qu'est-ce que je regarde ?"* —
  every panel must answer that.
- **Wire to the real pipelines** (the current front is a mock on demo data).

---

## 2. Decisions taken to unblock (open-questions resolved)

These were the load-bearing forks. Rulings below are **owner rulings of 2026-06-05**, backed
by the research pass. Those marked *(blueprint)* additionally need the blueprint to concur —
an ADR + blueprint note is the follow-up, tracked in §6.

| # | Question | Ruling | Follow-up |
|---|----------|--------|-----------|
| **OQ-4** | Tenor grid | **10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y** (the prof's grid; resolves the vision's `12m`/`1an` dup and out-of-order tail). | Pin as a data contract; add to blueprint data dictionary. |
| **OQ-1** | $-Greek convention *(blueprint)* | Store **raw per-unit Greeks** as source of truth; expose a **dollar layer** with explicit units: **Delta\$ = Δ·S·mult** (per \$1), **Gamma\$ = Γ·S²/100** (per 1% move), **Vega\$** per **1 vol point** (0.01), **Theta\$** per **calendar day** (÷365), **Rho\$** per **1% rate**. Per-contract (×mult) → per-position (×qty), additive across a book. | Make the **gamma normalisation (1% vs \$1)** and **theta day-count (365 vs 252)** explicit config flags. ADR to formalise. |
| **OQ-2** | Historical data source | **IBKR is the source** — owner/prof mandate (Yahoo excluded as unreliable). **Underlying daily price history** (index + every constituent, for the charts) is fully feasible via IBKR historical bars (TWS `reqHistoricalData` / Client Portal `/hmds/history`): years of daily depth, ~51 requests, within pacing — but **not yet implemented in our adapter** (`cp_rest_adapter.py` is live snapshot + WS streaming only). **Deep option-chain history** is IBKR's genuine weak spot (expired contracts, pacing), so the options dataset is built **forward** by the daily close-snapshot capture, with IBKR best-effort backfill at the start. **No third-party vendor by default.** | Build the IBKR **historical-bar fetch** path (P0.3 / 1C). Revisit an external vendor **only if** a deep options backfill is later proven necessary and IBKR insufficient — prof's call. |
| **OQ-3** | Index membership *(blueprint)* | **Point-in-time membership is mandatory.** Store each constituent with `(effective_add_date, effective_remove_date)` and as-of weights; never apply today's list to past dates. Source: **Siblis Research** (covers SX5E **and** SP500 with dated changes, ~\$50–100/mo), cross-checked against **STOXX** review history for SX5E, **EODHD/CRSP** for SP500. | Gate every historical join with `check-lookahead-bias`; this fits the existing `(instrument_key, as_of_date)` key. |
| **Futures** | Capture vs derive | **Forward = derived, primary** — backed out of the option chain via put–call parity (forward + implied rate + implied dividend per expiry), as the blueprint already does (`ForwardCurvePoint`). **Listed futures = captured, secondary** — for carry/roll, hedge instrument, and a cross-check on the option-implied forward. | ⚠️ **Futures are not in the blueprint.** Capturing them needs a **blueprint amendment + new ADR** and a contract (extend `ForwardCurvePoint` or a new `FuturesPoint`) before build. Until then, the forward path is unblocked and sufficient for analytics. |

**Why these, briefly.** The forward (not the listed future) is what Black-76 pricing,
implied vol, forward-delta and implied-dividend all reference, and backing it out of the chain
keeps it self-consistent with the market's repo/dividend — reconstructing from external
spot+rate+div curves injects basis error (OptionMetrics implied-dividend methodology;
put–call parity). The $-Greek units above are the dominant equity-derivatives desk
conventions; the two places desks genuinely diverge (gamma 1%-vs-\$1, theta 365-vs-252) become
flags, not assumptions. IBKR's pacing is no obstacle for **underlying daily bars** (one
request returns years; ~51 instruments is trivial), so the ticker charts are squarely IBKR's
job; the pacing/depth limit bites only on **historical option chains** (expired contracts),
which is why the options dataset is grown **forward** from daily snapshots rather than
back-filled from a vendor. Point-in-time membership is the difference between an honest history
and a survivorship/look-ahead-biased one — for dispersion (index vol vs the *right* basket
that day) it is the whole signal.

---

## 3. The build — phase by phase

Each workstream names: **goal**, **what exists today**, **what to build**, **acceptance**, and
**depends on**. The middle math (IV → surface → Greeks) is **already built and pure** — this
roadmap is mostly *amont* (upstream universe/membership/capture) and *aval* (cron, front,
risk views). The blueprint's own 16-step build is the analytics-core reference
([`blueprint/03-roadmap-16-steps.md`](blueprint/03-roadmap-16-steps.md)); we layer on it, not
re-derive it.

### Phase 0 — Contracts & unblockers (no code until these are pinned)
- **P0.1 Pin the tenor grid** (OQ-4) into the blueprint data dictionary and config.
- **P0.2 Pin the $-Greek units + flags** (OQ-1) into the risk contract and the BFF metric
  contract (each dollar number carries a unit string).
- **P0.3 Build the IBKR historical-bar fetch** for underlying daily history (index +
  constituents); confirm forward daily-snapshot capture as the options-history strategy (OQ-2).
- **P0.4 Decide futures capture** — write the ADR + blueprint amendment, or defer futures to a
  later increment and ship forward-only first.
- **Acceptance:** OQ-1/2/3/4 moved to *Resolved* in [`.agent/open-questions.md`](../.agent/open-questions.md); ADRs accepted where flagged.

### Phase 1 — Tab 1: the data foundation

| WS | Goal | Exists today | To build | Acceptance |
|----|------|--------------|----------|------------|
| **1J Index registry** | tell the scheduler **which indices** to fetch + each one's IBKR contract ref + exchange calendar | `universe.yaml` is a flat demo stub (`underlyings: [AAPL,MSFT,SPY]`); no index list, no per-index schedule (OQ-8/9 — [ADR 0035](../.agent/decisions/0035-index-registry-and-per-index-capture-schedule.md)) | an `indices:` block in `universe.yaml` (SX5E + SPX seeded), a typed `IndexRegistry`, and a calendar resolver over `exchange_calendars` (per-index session close) | adding an index is a one-entry edit the cron picks up; unknown calendar code rejected; per-index close resolves with correct tz/holidays |
| **1A Universe & membership** | index → point-in-time constituents → per-name chain | `infra/universe` discovery, `ChainSelection`, `(instrument_key, as_of_date)` key | index→constituent **membership** reference data with add/remove dates (OQ-3 source); ingest + as-of resolver | as-of join returns the correct historical basket; `check-lookahead-bias` passes |
| **1B Delta-band selection** | strikes = 30Δ put → ATM → 30Δ call per tenor | `ChainSelection` is **%-of-spot only** (`strike_window_pct=0.35`, `chain_planning.py`) | a **delta-band `ChainSelection` variant** beside the %-of-spot one | per tenor, selected strikes span the listed 30Δ put→call window; count varies with listing density |
| **1C Capture (daily close + history)** | one immutable close snapshot/day, index + all names; **plus underlying daily price-history** | the Nautilus actor (ADR 0023/0025), live/recent capture; **no underlying-bar capture** (`store_serving.py` ships `stock_snapshots=[]`); **no historical fetch in the IBKR adapter** (`cp_rest_adapter.py` = live snapshot + WS only) | a **daily close-snapshot capture mode** on the actor **and** an **IBKR historical-bar fetch** for underlying daily OHLC (index + constituents) | a day's run writes one provenance-stamped snapshot set; a backfill run populates years of daily bars per ticker |
| **1D Futures TS (gated)** | listed futures term structure 10d→3y (secondary) | **nothing — not in blueprint** | *if P0.4 says go:* contract + capture for futures points | futures TS captured and cross-checks the option-implied forward within tolerance |
| **1E Raw store** | immutable parquet system of record | `infra/storage` ParquetStore; **no daily-OHLC / price-history table** in `infra/contracts/tables.py` | parquet stays the record; **add a `DailyBar` (full OHLC) price-history contract** distinct from the option `MarketStateSnapshot` — OHLC makes a candlestick chart free | snapshots + daily bars land immutable, partitioned, re-readable |
| **1F Analytics projection** | IV→surface→(tenor×Δ-band) grid; Greeks decimal+\$ | `infra/{forwards,iv,surfaces,pricing,risk}` (pure, built) | a **projection** onto the pinned (tenor × delta-band) grid; carry **both** Greek representations with units (OQ-1) | grid output matches golden fixtures byte-for-byte; \$ + decimal both present, unit-tagged |
| **1G Cron** | unattended daily close capture | `infra/orchestration` (`jobs.py`, `pipeline.py`, `run_state.py`) | the **daily scheduled** close-capture job wired into the EOD pipeline | cron fires daily; `run_state` ledger shows idempotent, gap-free runs |
| **1H QC plane** | every stage validated + stamped | `infra/validation`, `infra/actor/stamping.py`, `orchestration/qc_job.py`, `alerts.py`, `dashboard.py` | extend QC checks to the new grid (coverage floor per tenor, Δ-band completeness) | QC gates pass; alerts fire on missing partition / coverage breach |
| **1I Front page 1** | pick index → scrollable constituent list → pick ticker → **chart + max analytics** | `apps/frontend` BFF mock; `/api/market` returns the option dashboard only — **no price-history field, no component list** | a **per-ticker OHLC price-history endpoint** feeding the **candlestick chart** (line is an acceptable fallback); the **scrollable constituent list** from membership (1A); **3D vol surface**; **dollar Greeks**; accordion + smile per maturity; **price-first** ordering; wire to the real pipeline | operator picks the index, scrolls constituents, selects a ticker and sees its real daily chart beside the analytics; every panel self-labels |

**Phase-1 dependency order:** P0 → **1J (index registry)** → 1A+1B (universe/selection) → 1C (capture)
→ 1F (projection) → 1G+1H (cron+QC) → 1I (front). 1J is foundational (feeds 1A/1C/1G/1I); 1D is
parallel and gated on P0.4. 1E is a no-op.

### Phase 2 — Tab 2: risk & strategy

| WS | Goal | Exists today | To build | Acceptance |
|----|------|--------------|----------|------------|
| **2A Basket builder** | select stocks+options into a position | — (front + a position model) | basket construction UI + a typed position model (straddle, strangle, …) | a multi-leg position is composed and priced from Tab-1 analytics |
| **2B Stress / scenario** | spot ±50 %, vol ±50 % → PnL surface | `infra/risk` **scenario engine + versioned grid** (ADR 0006) | wire the **±50%/±50% grid** to the basket; render the PnL surface | PnL surface matches a full reprice on the scenario grid |
| **2C PnL attribution** | dPnL by Greek (Δ/Γ/Vega/Θ) | `infra/risk` aggregation; full-reprice truth (ADR 0006) | attribution decomposition view per position/book | attribution sums to the full-reprice PnL within tolerance |
| **2D Strategy composition** | combine decorrelated sub-strategies | — | a book view layering positions + combined Greeks/PnL | book Greeks are additive across positions; combined PnL surface renders |

### Phase 3 — Execution (sketch only, gated)
- **3A Ticket** construction from a basket. **3B Order signing** (email) + **send**. Read-only /
  paper until an explicit owner gate; route through the existing broker seam (Saxo/Deribit/IBKR
  adapters), never a new ad-hoc path.

---

## 4. Sequencing & critical path

```text
P0 (contracts) ──► 1J registry ──► 1A membership ─┐
                                   1B Δ-band ──────┼─► 1C capture ─► 1F projection ─► 1G cron ─► 1H QC ─► 1I front
                   (1D futures, gated, parallel)                                                              │
                                                                                              ▼
                                                                 Phase 2: 2A basket ─► 2B stress ─► 2C attribution ─► 2D book
                                                                                              │
                                                                                              ▼
                                                                                 Phase 3 (sketch): 3A ticket ─► 3B sign/send
```

**Load-bearing blockers:** the **IBKR historical-bar fetch** (OQ-2 — without it there is no
backfill and no ticker chart; our adapter is live-only today) and **OQ-3** (wrong membership →
the history is silently dishonest). Settle both in Phase 0.

---

## 5. What is genuinely new vs reused

- **New build:** the **index registry** + exchange-calendar resolver (1J — which indices to fetch,
  their IBKR ref, per-index close), index→constituent **point-in-time membership** (1A), the
  **delta-band** selection variant (1B), the **daily close-snapshot** capture mode (1C), the
  **(tenor×Δ-band) projection** (1F), the **daily cron wiring** (1G), the **front history-pull + 3D
  surface** (1I), all of **Tab 2** (2A–2D), and — only if greenlit — **futures capture** (1D).
- **Reused as-is:** the pure analytics core (`forwards`, `iv`, `surfaces`, `pricing`, `risk`),
  `ParquetStore`, the QC/validation plane, provenance stamping, the orchestration job
  scaffolding, and the broker seam.

---

## 6. Still owned by the course / owner (not ours to settle)

- **Blueprint amendments** for the two *(blueprint)* rulings (OQ-1 $-units, OQ-3 membership)
  and for **futures capture** (the futures product is absent from the blueprint today).
- **Whether a deep options backfill is needed at all** (OQ-2) — default is IBKR + forward
  capture; an external vendor is a contingency only, and the prof's call.
- **Confirmation of the tenor grid** against the course's formal brief (OQ-4 ruling above is the
  prof's spoken grid; pin it on paper).

Detailed `tasks/` specs (C-series style) follow per workstream once Phase 0 closes.
