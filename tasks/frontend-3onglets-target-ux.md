# Target UX — the consolidated 3-onglets app (Données / Risque / Ordres)

**Status:** in design — owner-locked tab by tab (Vincent). **Land by Friday 2026-06-19.**
**Grounding:** `docs/transcripts/AlgoTradingCourse2-architecture-app-3-onglets.md` (THE structure:
3 tabs, `:17-25`/`:145-154`), `…Conseils-front-end.txt`, `…Greeks-et-strategies-vol.md`, the blueprint,
`docs/vol-surface/vol_surface_pedagogique.md`.

**Principle.** The app is **3 tabs only — Données → Risque → Ordres** (`architecture-3-onglets`).
Data first, the rest follows. **Legibility is the bar** (Conseils-front-end:47 "qu'est-ce que je
regarde ?"). Fast→deep within each tab. The current **7 tabs collapse to 3**. Cosmetics + small bugs
come AFTER this target UX is established and landed.

## Consolidation map (current 7 → intended 3)

| Current tab | Verdict (spec) | Folds into |
|---|---|---|
| Market | ✅ Onglet 1 (Données) | **Onglet 1** |
| Basket | ✅ Onglet 2 core (build→stress) | **Onglet 2** |
| Risk Scenarios | ❌ not a page (stress is on the basket) | → Onglet 2 |
| Positions | ❌ not a page (positions = the book) | → Onglet 2 |
| Strategy (backtest) | ❌ not a tab (backtest = Tab 3) | → Onglet 3 |
| Signals | ❌ absent from spec; overlaps Market | → dropped (lives in scorecards + ρ̄) |
| Operations | ❌ not a product tab (backend observability) | → secondary utility (owner's call) |
| *(no Orders tab)* | ⚠️ Onglet 3 missing as a tab | **create Onglet 3** |

## Onglet 1 — Données  ✅ LOCKED (2026-06-17)

One scrollable page; underlying picker at top drives everything (`Conseils-front-end:23`). Order
top→bottom:

- **⓪ Bande SCORECARDS** — ATM · skew 25Δ · convexité · RV−IV. The instant read (pédagogique §3.2
  "niveau/pente/courbure" + §4.2 term). **Requirement: design-system theme — legible, NOT a raw white
  background; a thin strip (4 numbers), aligned with the rest of the page.**
- **① Bloc PRIX** — index candlestick (OHLC) + **master-detail constituents** (weighted list left ·
  **selected member's candlestick** right, default = heaviest, cahier §3.2 — the "2nd candlestick").
  Restores the `vincent`-remote `ConstituentsWorkspace`, moved UP from the refactor's bottom placement.
- **② NAPPE 3D** — the vol gestalt (Plasma, display ceiling ~0.35, no `connectgaps`).
- **③ PANNEAU TÉNOR (10d…3y)** → for the selected tenor, the complete one-tenor picture:
  - **put/call smile** (log-moneyness, plain ticks) — the IV.
  - **price structure** — per strike: **bid / ask / volume** (NOT a mid average — Greeks transcript:14
    "bid/ask + volume"; lets the trader read the spread/liquidity). Option **price** per strike is part
    of this block (transcript `:68` "options … avec leur prix"). ⚠️ data wiring: `bid`/`ask` exist on
    the snapshot contract (`tables.py:44-45`) but are not yet in the analytics payload — the BFF must
    surface per-option bid/ask+volume.
  - **Greeks** — TABLE (deltas × greeks, raw+devise — *verified present*) **+ SHAPE CURVES vs strike**
    (option c, the §3.6 profiles: gamma/vega bell, delta S-curve — complementary, not redundant:
    curves = *where* it peaks, table = *how much*).
- **④ ρ̄ / dispersion** — secondary diagnostic (Eq. 23, realized vol).

**Futures: deferred (ADR 0037).** The transcript puts a futures multi-maturity grid in Tab-1; we ship
**without** it for a functional version now, and add a futures term-structure block later if time allows.
**Tenor navigation: selector, not the transcript's literal "accordéon"** — owner-decided + blueprint
Bloc 3 ("smile à maturité fixée"); same one-maturity-at-a-time content.

**Signals page: DROPPED.** No separate Signals tab in the spec; its content already lives in ⓪
(RV−IV/skew) + ④ (ρ̄).

Logic: **price layer (index + members) under a scorecard headline → vol read → vol depth → diagnostic.**

## Onglet 2 — Risque  ✅ LOCKED (2026-06-17)

Intent (`architecture-3-onglets:94-109`): **compose a book, THEN shock it** (±50% spot / ±50% vol /
±10% rate). Stress is the *second action on the basket*, same tab. Flow = **compose → see → shock →
explain**, one scrollable tab:

- **① Composer le book** — ergonomic basket builder: add legs (index options across the tenor×delta
  grid **+ constituent equities**), buy/sell, call/put/straddle/strangle. (= current Basket "Build&price")
- **② Le book** — composed positions + **combined $Greeks** + summary. **Folds in the Positions page**
  (book summary, open legs, fills ledger) — it is the input to the stress.
- **③ Choquer** — the **±spot / ±vol / ±rate** grid → watch P&L evolve (`StressSurface`, on-demand)
  **+ named historical scenarios** (2008 / covid) as shock presets. **Folds in Risk's named scenarios.**
- **④ Attribution** — **P&L by Greek** (Σ Greek × Δvariable; 1st order ~90% + 2nd-order residual —
  Greeks transcript §7).

**Broker reconciliation moves to Onglet 3** (it is a post-orders, book-vs-broker check, not a risk view).

## Onglet 3 — Ordres  ✅ LOCKED (2026-06-17)

Intent (`architecture-3-onglets:111-116`): **order send + backtest**. Flow = **book → ticket →
(passage) → vérifie**, with backtest as the validation tool:

- **① Ticket d'ordre** — compose/preview the order ticket from the book's legs, gated. (= the
  `TicketPanel` currently in Basket — moved here, its real home.)
- **② Passage / état des ordres** — send + track status. ⚠️ **3B (live transmit) does not exist and is
  gated** (security M2: the booking audit must be write-ahead before going live). Today = **paper /
  read-only**; the send button stays disarmed until the owner gate + M2 fix.
- **③ Réconciliation broker** — *(moved from Risk)* live book vs broker (positions/cash/fills): "does
  the broker agree?". Its real place is post-orders.
- **④ Backtest** — backtest the strategy over the offline store (cumulative P&L + by-Greek
  attribution). (= the Strategy page, folded in.)

**Operations** (launch a capture / system health) is **not a product tab** (blueprint = backend
observability). Kept as a **secondary utility**, not a top-level tab.
