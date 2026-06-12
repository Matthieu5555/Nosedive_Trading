# TARGET — what done looks like, and how it makes money

This is the destination document and the design standard. It records the owner's brief of
2026-06-12: how this infrastructure is supposed to make money, the strategy book it runs,
the analytics rulings that follow, the end-of-week deliverable, and the full capability map
a professional stack is measured against. The next person designing work reads **this file
plus `tasks/TASKBOARD.md`**, diffs the target against what exists, and cuts the next specs
from the gap (§7 is that gap list, pre-ordered).

Authority: the blueprint (`documentation/blueprint/`, ADR 0011) overrides on any formula,
field, or domain definition — the rulings in §4 that touch domain contracts need an ADR +
blueprint amendment before build, and say so. `AGENTS.md` governs process.

---

## 1. How this makes money

The infrastructure has one economic thesis: **options markets quote expectations, and the
money is in the measured gap between what is implied and what realizes.** Implied vol vs
realized vol. Index implied vol vs the implied vol of its weighted basket. Front-month vol
vs back-month vol. Put-side vol vs call-side vol. Each gap is a premium someone pays for
protection or convenience, and each can be harvested — *if you can measure it precisely,
enter it cheaply, and verify you are being paid by the gap you targeted and not by luck.*

That gives the edge chain, and every layer of the stack is one link in it:

```
capture (clean, point-in-time, QC-gated)
   → surfaces & curves (honest IV, forwards, rates — the measurement)
      → signals (the gaps: IV vs realized, index vs basket, term slope, put vs call)
         → strategies (rules that harvest a named premium when its signal says cheap/rich)
            → execution & booking (enter at a known price, position built from fills)
               → attribution (did the P&L come from the Greek we intended to hold?)
                  → allocation (compound the streams that pay; cut the ones that don't)
```

The differentiator is not order entry — it is **measurement and attribution**. A strategy
here is a contract: it names the premium it harvests, the signal that triggers it, the
Greeks it intends to hold, and its kill condition. Attribution (§5.2) enforces the
contract: if a "vol carry" book shows its P&L coming from delta, it is an off-thesis
directional bet and gets cut. The residual is the honesty meter — a large residual means
we do not understand our own book, which is a stop signal, not a footnote.

Precise analytics is not decoration on this: it *is* the edge. Retail loses these trades
to sloppy measurement (wrong forward, stale vol, survivorship-biased history, P&L it
cannot explain). Everything in §6 — determinism, as-of discipline, contracts, golden
tests — exists so the measured gap is real before money chases it.

## 2. The end-of-week goal

What we demo at the end of the week, all of it real (no mock data, no dead buttons):

1. **A hella clean frontend.** Every panel answers "what am I looking at", wired to the
   real pipeline. The front is the proof the rest exists.
2. **Several days of harvested data.** The EOD capture (SX5E + SPX) has run unattended and
   banked a gap-free, QC-clean history of close snapshots, surfaces, and Greeks.
3. **Enter a strategy.** Compose a position (legs from the captured chain), book it, and
   hold it as *the current position* of the book. The flagship to enter is the dispersion
   book (§3, S1).
4. **An order booking system that works.** Ticket → confirm → booked position, as one
   chain. Booking a position requires a **password** (an explicit human gate in front of
   anything that changes the book). Paper/read-only against the broker until the separate
   owner gate (3B) opens.
5. **P&L decomposition, strategy-level and portfolio-level.** For each strategy *and* for
   the global portfolio:

   ```
   dPnL = Delta + Gamma + Vega + Theta + Rho + Vanna + Volga + residual
   ```

   each term in **dollars**, residual measured against the full reprice. Rho is computed
   against a real risk-free curve (Euribor/€STR for EUR, SOFR for USD — ruling R1, §4),
   not an internal constant.
6. **A couple of decorrelated strategies** from the book in §3, composed and shown side by
   side with individual and combined P&L.

## 3. The strategy book, v1

Picked from the owner's brief and the Applied Options course
([`ThomasHossen/MM_options_trading.md`](ThomasHossen/MM_options_trading.md) — page refs
below), for **unrelated failure modes**, not unrelated names. The decorrelation claim is
verified, not assumed: the book view (2D + §5.8) must show cross-strategy P&L correlation
and shared-tail overlap, because two differently-named strategies can secretly be the same
trade.

| # | Strategy | Premium harvested | Intended Greeks | Dies when |
|---|----------|-------------------|-----------------|-----------|
| S1 | **Dispersion** (flagship) | correlation premium: index IV rich vs constituent IVs | long single-name gamma/vega, ~0 net delta | single names go quiet together (realized correlation ↑, single-name vol ↓) — theta bleed |
| S2 | **Index put line** (allocation factory) | index downside IV > realized | short downside vega, positive theta | sharp sustained drawdown (short left tail) |
| S3 | **Gamma trading** | realized vol > implied, on one cheap name | long gamma, delta-neutral by rule | quiet drift + IV crush (gain < theta) |
| S4 | **Covered short strangle** | range premium on a fundamental holding | positive theta, ~0 entry delta, long stock | big move either way in a name we chose to own |
| S5 | **Calendar carry** (optional) | front theta decays faster than back | short front / long back vega, positive theta | front-month event repricing (term structure inverts) |

S1 and S3 share a failure mode (low realized vol) — held *because* the book view must
prove it can see that overlap. S2 is the deliberate opposite tail to S1; together they are
a relative-value position on index-vs-single-name vol, which is the point.

### S1 — Dispersion (the owner's spec)

**Construction (v1):** buy ATM straddles on the **top-10 SX5E constituents by index
weight** (point-in-time weights, 1A membership), and **short the index future** sized to
flatten the basket's net dollar delta. Long single-name gamma and vega; the future leg
removes market direction, so the P&L engine is single names moving while the index doesn't.

**The signal — implied correlation.** Index variance relates to constituent variances via
the basket identity (blueprint Eq 23 — the primitive already exists in `risk/basket.py`):

```
σ²_index ≈ Σᵢ wᵢ² σᵢ²  +  Σᵢ≠ⱼ wᵢ wⱼ σᵢ σⱼ ρ̄
```

With the index ATM IV and each constituent's ATM IV captured (same tenor), solve for the
**average implied correlation ρ̄**. Enter when ρ̄ is rich (index vol expensive relative to
the names — the dispersion premium is on offer); harvest as realized correlation comes in
below it. This is the cleanest example of "precise analytics is the edge": the signal does
not exist without per-name surfaces and a trustworthy index surface on the same grid.

**Hedging discipline:** per-name delta drift is re-flattened by rule (band-based, like S3);
the future leg re-sized at each rebalance. Attribution must show P&L in single-name gamma
and vega, near-zero in net delta.

**v1 → v2:** the classic structure shorts **index vol** (short index straddle), making it
a pure correlation spread. v1 as specced shorts the **future** (delta only), so it stays
net long vol — simpler to run, dies faster in quiet tape. v2 (short index straddle leg) is
the natural upgrade once v1's attribution is trusted. Until futures capture lands (1D,
gated), the short-future leg is a **synthetic short forward from the index chain** (short
call + long put, same strike/expiry) — priced off put–call parity, which the pipeline
already trusts to back out forwards.

**Infra it needs (gaps):** constituent **option** capture for the top-10 names (today we
capture index options + constituent OHLC bars only — this is the single biggest new lane);
implied-correlation signal on top of Eq 23; the synthetic-forward leg builder; point-in-time
top-10-by-weight resolution.

### S2 — Index put line (Allocation Factory, course p.128–130)

Systematic short-put production line on the index: sell one ~3%-OTM (≈25Δ), ~30-day put
per day; **line capacity** caps open contracts (course: 30, rolling so one expires daily);
**steering rule** moves the strike distance (2.5% / 3% / 4% below market) to control
assignment frequency; the course's 2021 run and 2008 stress (p.130) are the reference
behavior — the 2008 page exists precisely because 2021-style results are a draw from a
friendly tape. Margin/assignment capacity is sized up front (the course's InvWC number),
which is why margin forecasting is on the capability map (§5.9). Kill condition: drawdown
or vol-regime trigger flattens the line — this strategy is the reason the book needs the
stress screen (§5.4) and a kill switch (§6).

### S3 — Gamma trading (course p.107–108)

Delta-neutral, gamma-positive on **one** name whose vol is cheap: course setup ranking —
best is *low IV expected to rise*; worst is high IV about to fall. Construction: long call
+ short stock (or long put + long stock) to Δ=0; rebalance in delta bands (the p.108 cycle:
sell strength in clips as delta rises, buy them back lower; each round trip banks the
rectangle). P&L = scalp gains − theta, with vega as the kicker or the killer. The entry
signal needs **IV rank/percentile per name** (course p.36) — which needs banked IV history,
i.e. the harvested days are the signal's raw material.

### S4 — Covered short strangle (course p.56–58)

On a name we fundamentally want to own (course rule: "the long position requires a good
fundamental story"). Buy ¼–½ of the desired position; sell OTM put + OTM call at 30–45d
with net Δ≈0; roll monthly in the middle state; put assignment averages in at
EPP = X − P₀ − C₀, call assignment exits at ESP = X + P₀ + C₀. It is a cycle, not a trade.
Decorrelated from S1–S3 by driver: its risk is idiosyncratic to a chosen holding, not to
the vol complex.

### S5 — Calendar carry (course p.42–45, optional fifth)

Short front-month / long back-month at the same strike: positive theta from the front
decaying faster, long back vega. Entry reads the **term structure** panel (slope/contango)
the front already renders. The parity identity on p.45 is the consistency check the
pricing layer already enforces.

## 4. Analytics rulings (owner, 2026-06-12)

Three rulings that change domain contracts. Each needs an ADR + blueprint amendment before
build (ADR 0011); recorded here so the specs are cut from one place.

**R1 — Real risk-free curves; Rho against Euribor.** Today the only rate in the system is
the **parity-implied per-expiry rate** backed out of the option chain (`infra/forwards`),
and rho is computed against it. Ruling: ingest an explicit **per-currency risk-free
curve** — **Euribor/€STR pillars for EUR** (SX5E), **SOFR for USD** (SPX) — as a daily
captured, as-of table. Uses: (a) **Rho** is the sensitivity to *this* curve, bumped per
currency — a book-level "rates +50bp" answer is meaningless against a per-expiry implied
rate; (b) the **spread parity-implied − risk-free** becomes a first-class diagnostic
(implied funding/dividend/borrow signal, and a QC gate on the forward estimation). The
parity-implied rate stays the pricing-consistency rate; the external curve is the *risk*
rate. Contract: a `rates` table (currency, pillar tenor, rate, as-of); config names the
source per currency.

**R2 — Two vol surfaces: puts and calls fitted separately.** Today one surface is fitted
per underlying per day. Ruling: fit **three** — put-side, call-side, and the combined
reference surface — and carry `surface_side ∈ {put, call, combined}` through the surface
contract, projection, BFF, and front (side toggle on the 3D surface and smiles). Why this
makes money rather than just more plots: the **put−call IV spread per (tenor, strike)** is
a signal and a QC instrument at once — persistent spread = forward/dividend/borrow
mis-estimate or a genuine funding skew (both tradable information); a blowout = bad data
quarantined before it reaches a strategy. Puts price puts and calls price calls; the
combined surface remains the forward-backing and attribution reference.

**R3 — Implied correlation as a first-class signal.** From R2-grade per-name surfaces and
the index surface, compute and persist ρ̄ per tenor (§3, S1) daily. This is the dispersion
entry signal and a market-state diagnostic (correlation regime) for the whole book.

## 5. The full capability map

Six layers plus two that retail systems skip. Against each: where we stand (state as of
2026-06-12, per the taskboard; verify before building on any row).

### 5.1 Position & risk engine — the core

Positions by instrument and underlying; net/gross exposure; Δ, Γ, Vega, Θ, Rho;
second-order Greeks (Vanna, Volga, Charm, …); Greeks by maturity, strike, underlying — in
**natural units and dollars** both. "Book Vega = +$42,000 per vol point" translates into
P&L impact; "+3,281" does not.

*State:* largely built (`infra/risk`, `pricing/dollar_greeks.py`, book-additive; front
matrix per maturity). Missing: a **position store fed by fills** (today a "position" is a
composed basket, not a booking result), Vanna/Volga as first-class outputs, Rho against R1.

### 5.2 P&L attribution engine — the differentiator

Daily P&L decomposes into the named terms of §2.5, per position, per strategy, per book,
drillable; full reprice is the oracle; the residual is the honesty meter. Attribution is
what *enforces the strategy contracts* of §3.

*State:* seam landed (2C — Δ/Γ/Vega/Θ + residual on a scenario shock). Missing: Rho, Vanna,
Volga terms; attribution of *realized* day-over-day dPnL; strategy-level grouping (2D).

### 5.3 Market data & surface engine — usually the hardest

Spot, futures curves, **rates curves (R1)**, dividends, vol surfaces **per side (R2)**,
history; surface visualization, *changes*, shocks, fit diagnostics. The trader's constant
question — "did I make money because the stock moved, or because the 25-delta put wing got
richer?" — is answerable only here.

*State:* our strongest layer (capture → IV → SVI → tenor×Δ-band projection → front, with
QC + provenance). Gaps: tenor coverage re-capture pending; **constituent option capture
(S1's blocker)**; rates curves (R1); per-side surfaces (R2); futures (1D, gated; synthetic
forward bridges it).

### 5.4 Scenario & stress testing — the risk manager's screen

Spot ±X%, vol ±X pts, **rates ±X bp (needs R1)**, correlation shocks, named historical
scenarios (2008, COVID).

*State:* spot×vol full-reprice grid built (2B, on the front). Missing: a real rate axis
(T-scenario-rate-axis), correlation shock (meaningful once S1 exists), named scenarios.

### 5.5 Execution & OMS

Order entry → fill → position → risk → P&L as **one continuous chain**; the book is built
from *fills*, never from intentions. Partial fills, broker reconciliation.

*State:* the missing layer. `packages/execution` is empty; 3A/3B specced. This week: 3A +
the password-gated booking step so the chain exists in paper form end to end.

### 5.6 Portfolio analytics

Why do I own this; which positions contribute risk vs return; factor exposure. For a quant
book, factor attribution often matters more than instrument attribution.

*State:* not started by design (post-week). Per-underlying/per-family attribution views in
`risk/` are the seed.

### 5.7 Backtesting — not optional

Two machines: a **research backtester** ("does this idea have edge?") and a **production
shadow** ("would my live system have traded and produced the P&L I expect?" — it catches
implementation errors; a strategy is not real until backtest, paper, and live share the
same logic). For options this means replaying full point-in-time market state — surfaces,
not just prices; realistic fills; expiry/assignment; margin; costs — and the serious output
is performance, drawdowns, turnover, exposure, Greeks, stress losses, **and attribution
through time** ("returns came from short vega and positive carry", not "Sharpe 1.4").
First concrete target when this layer opens: replay S2 through a banked stretch and an
adverse regime, the course's own 2021-vs-2008 method (p.129–130) industrialized.

*State:* substrate genuinely ready (immutable raw, byte-identical replay, as-of discipline,
same actor live/replay). The backtester itself does not exist. Natural next big build.

### 5.8 Portfolio construction — the allocation layer

Not "five strategies" — five **independent sources of P&L**, diversified by failure mode
(§3's table is the design artifact). The infrastructure must show strategy-level P&L,
cross-strategy correlation, factor overlap, shared tail risk, capital allocation, marginal
contribution to risk and Sharpe. The admission question for any new strategy: "does adding
this improve the portfolio after costs, capacity, and drawdown interaction?" A mediocre
standalone Sharpe can be excellent if genuinely uncorrelated; a high Sharpe can be useless
if it loads on risk we already hold.

*State:* 2D specced on landed 2A/2B/2C. Correlation/overlap analytics post-week; §3 gives
them their first real test data.

### 5.9 What sophisticated desks additionally expect

Intraday VaR / expected shortfall, liquidity and concentration risk, **margin forecasting**
(S2 needs it first — its capacity rule is a margin number), financing/borrow costs,
real-time alert delivery, historical replay ("the book at yesterday 2:17 PM"), explainable
drill-down, position lineage (which trade created this risk?). The 2026-06-08 autonomy
audit already flags the operational slice: alert delivery, kill-switch, reconciliation,
unattended re-auth.

## 6. The robustness standard

How we build §5 without it collapsing under one person's maintenance. Detail lives in
`.agent/conventions.md` and `tasks/TESTING.md`.

- **One modular monolith, few deep modules.** One repo, one gate, layering enforced by
  import-linter (`core ← infra ← brokers ← {strategy, execution} ← frontend`). Narrow
  public APIs, boring internals.
- **Contracts at every seam.** Every arrow in
  `data → signal → portfolio → risk → execution → accounting → reporting` has an explicit
  typed schema — fields, units, timestamps, identifiers, null/failure behavior
  (`infra/contracts` is the frozen seam; pydantic at the BFF edge).
- **Four test layers.** (1) unit tests on pure logic, expected values derived
  independently; (2) **contract tests** between modules — catch the renamed field, the
  shifted unit; (3) integration on a tiny historical dataset, raw → trades → P&L →
  attribution; (4) **golden/regression** — fixed scenario, fixed output; 37 trades and
  Sharpe 1.21 yesterday does not become 42 today without a knowing change.
- **Quant guards.** No look-ahead (as-of everywhere, `check-lookahead-bias`), no
  survivorship bias (point-in-time membership), no data-snooping dressed as research.
- **One logic, four contexts.** Research, backtest, paper, and live call the same strategy
  object. The notebook explores; it never becomes a second implementation.
- **Accounting from fills.** Not orders, not signals.
- **Operational checks.** Data freshness, stale-price detection, broker position/cash/fill
  reconciliation, pre-trade limits, kill switch, append-only audit log, replay. Target:
  not perfect code — **deterministic, replayable, testable, auditable** code.

## 7. The gap list — what the next specs are cut from

Ordered by (this week's goal first, then what the strategy book needs, then the layers).
Each row is roughly one spec.

1. **Booking chain (week):** 3A ticket + password-gated booking → a fills-based position
   store the risk/attribution engines read (§5.1/§5.5). The password gate is the book's
   write barrier.
2. **Attribution completion (week):** Rho/Vanna/Volga terms + realized day-over-day
   attribution + per-strategy grouping (§5.2, extends 2C; Vanna/Volga need the pricing
   layer to emit them).
3. **Strategy composition (week):** 2D as specced — the §3 book composed, combined Greeks
   + stress + attribution, correlation view (§5.8).
4. **Constituent option capture:** top-10 SX5E names through the existing capture lane —
   S1's blocker and R3's input.
5. **Rates curves (R1):** Euribor/€STR + SOFR ingestion, as-of table, Rho rework, the
   implied-vs-riskfree spread QC. ADR + blueprint amendment.
6. **Per-side surfaces (R2):** put/call/combined fit + `surface_side` through contract →
   BFF → front; put−call spread QC + signal. ADR + blueprint amendment.
7. **Signal layer:** implied correlation (R3), IV rank/percentile per name, realized-vs-
   implied spread, term slope — persisted daily, the strategy entry inputs (§1's chain).
8. **Backtester:** research first, production shadow second (§5.7); S2 on banked history
   is the first case.
9. **Operational hardening:** margin forecasting (S2), alert delivery, kill switch,
   reconciliation (§5.9 + autonomy audit).

## 8. How to use this file

Reference it, don't restate it. `BIG_PICTURE.md` says how we build the backbone with the
least code; the roadmap (`documentation/roadmap-index-analytics.md`) sequences the current
phase; this file holds the finish line and the money thesis both point at. When a target
item lands, update its *state* line here in the same change — a stale target is worse than
none. When the owner moves the goal, this file moves first.
