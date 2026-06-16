# Task — Page 1: the definitive "understand the market fast" reading model

**Status:** open — **PRIORITY / P0** (2026-06-16). Owner ruling: Vincent (design locked this session).
**Lane:** `frontend-` (web + BFF reads). **Tasking-first** — implement only after this spec is agreed.
**Relationship to the other page-1 tasks (no duplication):**
- [`frontend-page1-cdc-0051-correction`](frontend-page1-cdc-0051-correction.md) (**in progress, Matthieu's lane**) owns the
  ADR-0051 *structural* fixes (drop the constituent-as-underlying axis, ρ̄ ← realized
  `strategy_signals`, day-selector → one canonical close/day). Those are **prerequisites**; this
  spec is the **target layout/reading model** they build toward.
- [`frontend-page1-cdc-buildout`](frontend-page1-cdc-buildout.md) is the CDC block inventory; this spec
  supersedes its *layout/reading-order* parts.

## Principle

The page answers one question for an options trader: **"what is the market doing, fast?"** Reading
gradient = **fast → deep**: a 4-number state, then the gestalt, then drill into one tenor. Legibility
is the bar (transcript: *"qu'est-ce que je suis en train de regarder ?"*).

## The locked reading model (top → bottom, one scrollable page — transcript: *"tout scrollable"*)

1. **Price** — index candlestick + spot/move. Context only.
2. **⭐ Scorecards** (the instant read) — **niveau ATM**, **skew 25Δ**, **convexité**, **RV−IV**.
   Pedagogique §3.2 ("niveau / pente / courbure résument l'intégralité du smile") + §4.2 (term =
   *le quand*). Read from the persisted **`StrategySignal`** (`iv_rank`, `rv_minus_iv`, `term_slope`)
   — already built, do not recompute. Index-keyed, side-agnostic; the **put/call asymmetry is the
   skew (25Δ risk-reversal = IV_put − IV_call)** — no put/call split of the cards.
3. **3D nappe** — the all-maturity **gestalt** (§4.1 "surface = empilement des smiles"): is it the
   normal world (skew + contango) or **deformed** = signal (§4.3). Intuition layer, not the chiffrage.
4. **── Tenor selector (10d · 1m · 3m · 6m · 12m · 18m · 2y · 3y) ──** — **one shared control** that
   drives **everything below**. The grid is the authoritative `tenor_grid` (`09-data-dictionary:15`,
   ADR 0011). Tenors beyond the captured span render as **labelled `ProjectionGap`**, never hidden
   (§4.5, `19-final-reminders:9`).
   - **Smile** for the selected tenor — blueprint reads the smile **à maturité fixée** (Bloc 3),
     log-moneyness x-axis (§02:41). **Put + call superimposed** (two curves; the **gap = the skew**)
     — *this overlay is ADR 0048 (per-side `surface_side`, put−call IV spread), an enrichment of the
     blueprint's single combined smile; `combined` stays available as the reference shape.*
   - **Greeks table** for the selected tenor — deltas-as-rows × greeks-as-columns (**raw + devise**),
     read from `ProjectedOptionAnalytics` (tenor × delta-band grid). Band rows come from the
     projection `band_labels` (config, not a blueprint-pinned grid).
5. **ρ̄ / dispersion** — secondary diagnostic strip (Eq 23, **realized** vol, full membership — ADR
   0051). **Never leads** (leading it is the transcript's "qu'est-ce que je regarde ?" failure).

**Net change vs today:** all-tenor 2D spaghetti and the multi-panel browser → **3D nappe (gestalt) +
ONE tenor selector → {put/call smile + greeks} for that tenor**. The accordion idea is dropped in
favour of the single selector (more compact, blueprint-aligned to "maturité fixée", kills the
curves-vs-table redundancy).

## Visual / correctness fixes (from the 2026-06-16 bilan, vincent baseline vs current)

1. **Drop the 2D heatmap** — redundant with the 3D nappe (same lattice/scale). Owner ruling over CDC §3.4.
2. **Nappe colour ceiling** — split `SURFACE_Z_MAX` into a *reject* threshold (~0.6, data sanity) and
   a **display** ceiling (~**0.35**, the SX5E live band). Today's single 0.6 washes the skew/term into
   the lower half of the Plasma ramp (`charts.tsx:24`).
3. **Plain tick formatting** — drop `.2e` scientific on the smile + ATM-term axes (`charts.tsx:415-416,388`):
   log-moneyness in decimal `k`, IV in `%`. Trader units, not `-3.00e-1`.
4. **No `connectgaps`** on the dense surface/heatmap (`charts.tsx:99,267`) — it re-fills the holes
   punched for bad data; the blueprint says **show** where coherence breaks (§4.5).
5. **One scrollable page**, not Analytics/Data-quality tabs (`Market.tsx:151-156`) — restore the
   price-first vertical scroll (transcript l.19/31/49).
6. **Delete dead code** — `DollarGreeksMatrix` / `DollarGreeks` / `MaturityAccordion` (orphaned,
   tests-only) and the orphaned `VolHeatmap` once the heatmap is dropped.

## Acceptance

- Page is **index-keyed only** (no `/api/analytics?underlying=<member>`); one scrollable column.
- Scorecards render 4 numbers from `StrategySignal`; skew is the 25Δ risk-reversal.
- One tenor selector drives the smile + greeks; tenor grid = the pinned 8; uncaptured tenors show as
  labelled gaps.
- Smile = put+call overlaid on a log-moneyness axis, plain ticks; combined available.
- Nappe display ceiling ≈0.35, no `connectgaps`, dead components removed.
- Web gate green (lint + vitest + tsc).

## Provenance (honest grounding)

🟢 blueprint-hard: tenor grid, smile-at-fixed-maturity, nappe=stacked-smiles, log-moneyness,
show-the-gaps, ρ̄=realized (Eq 23 / ADR 0051). 🟢 reference (pedagogique §3.2/§4.2): the scorecard
metrics. 🔵 already built: `StrategySignal`, `ProjectedOptionAnalytics`. 🟡 ADR-level (not blueprint):
put/call smile overlay = ADR 0048. 🟠 intent (transcript/CDC): reading order, scrollable, the §3.3
block. Drop-heatmap = owner ruling.
