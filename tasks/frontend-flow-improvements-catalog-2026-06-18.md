# Frontend flow — improvements catalog (review artifact)

> **STATUS: DRAFT v0.1 · NON-BINDING · NOTHING VALIDATED.**
> This is a *catalog* to keep and refine, not an approved work plan. No item here has
> a green light. Owner (Vincent) has validated nothing; "this should be improved" is the
> strongest claim any line makes. **This doc should itself be improved** as items are
> understood, ruled on, or assigned. Edit freely.

**Date:** 2026-06-18 · **Scope:** the 7-tab front on `main` (Market/Basket/Signals/
Strategy/Risk/Positions/Operations). **Decision context:** the front stays on Matthieu's
`main`; the `cockpit-risk-refonte-review` branch (3-onglet shell, single-scroll stress hero,
BookContext, Ordres tab) is **retired**. See `main-is-canonical-front-branch-retired` memory.

**Working-tree state at review time:** clean (only this doc + an untracked `diag-shots/`
folder). Matthieu's last commit was ~8 h before review — **nothing in flight** on these files.

**How this was produced:** a static "part audit" + a 4-lens critic panel (blueprint/transcript
conformance · options-trading logic · UX fluidity · correctness/grounding) + an item-by-item
verification of every previously-planned fix against the code. All four critics returned
*"Réserves"*: foundation solid, no redesign — the items below are the residual gap.

---

## 0. The "run_id" question — CLOSED, non-issue (recorded so it is not re-raised)

A critic flagged as P1 that `/api/analytics` "accepts `run_id` but never filters on it", so two
runs on the same `trade_date` would return a silent union. **Verified false / by-design:**
- `read_for_underlying` (`store_reads.py:24`) takes only `trade_date`/`provider` — **no `run_id`
  param exists**; `get_analytics` (`analytics.py:298`) takes only `trade_date`+`underlying`.
- Run-partitioning was **deliberately removed** across the BFF — commit
  `42308d1 "C2 complete — remove run-partitioning across the BFF (overwrite-last-wins)"`, with tests.
- **Owner-confirmed:** multi-run-per-day was a pre-ingestion-fix artifact. The pipeline now writes
  **one capture per day and overwrites the slot**, so there is never more than one run per
  `(trade_date, underlying)` to union. `run_id` in the `Frame` is provenance-only.

→ No defect. *(Latent, separate, already-tracked hazard: "intraday dry-run pollutes prod slot" —
an upstream invariant question, not a BFF bug, and out of scope for the front.)*

---

## 1. Already landed — do NOT re-open

The bulk of the old punch-list (16/06 bilan, ADR-0051 corrections, scorecard band, visual identity,
send-verb) shipped on main. Confirmed with file evidence:

- **Reading model (RM-01..13):** one scrollable column; scorecards from persisted `StrategySignal`
  (never recomputed); ONE shared tenor selector (`TENOR_GRID` 10d·1m·3m·6m·12m·18m·2y·3y) driving
  smile + greeks; smile = put/call wings overlaid on log-moneyness (gap = skew); ρ̄ strip trails;
  page index-keyed only.
- **Bilan visual (VX-01..05):** 2D vol heatmap dropped; **nappe/smile holes shown via
  `connectgaps:false`** (the "trou dans le smile/nappe" fix is in); display ceiling 0.35 vs reject 0.6;
  plain `.2f`/`.0%` ticks (no `.2e`); dead code (`DollarGreeksMatrix`/`MaturityAccordion`/`VolHeatmap`)
  removed.
- **ADR-0051 (C0-01..06):** canonical close default; constituent-as-underlying axis removed;
  dispersion reads persisted realized-vol ρ̄.
- **Visual identity (D-01/02/05/07/08/09):** house "Nappe" colorscale (not Plasma); ATM ridge as
  `data[1]`; band gradient echo; sci→readable formatting; lede 75ch + scroll-snap; colorbar `.0%`.
- **Scorecards (B-01..06):** 6-card band (ATM · term slope · IV-rank · skew 25Δ · RV−IV · ρ̄);
  convexity demoted to the smile block; sign legend printed; "vp" defined; 4/6 cards from `/api/signals`.
- **Send-verb (SV-01):** unified to "Send to broker".
- **Guarantees that hold:** assistant numbers hard-grounded (`assistant.py:81` post-check, 1e-9,
  canned refusal on drift); chart gap contract; async state coverage (skeleton + named error + named
  empty on every block); skew sign correct (`scorecards.ts:121-124`, IV_put − IV_call); headers read
  fast→deep.

---

## 2. Candidate improvements still on the table (non-binding)

Severity is the reviewer's suggestion, **not an owner priority**. "id" links to the source spec
where one exists; `F-*` are new panel findings.

### 2a. Sign-colour law — incomplete (the cluster that recurs)
*(old D-03 + critic "trading-logic")* The `signColor` law (a branch delta that main's Scorecards
rewrite simplified away) is applied on the scorecards but missing elsewhere:
- Greeks table cell values render with no positive/negative class (`DollarGreeksByMaturity.tsx`).
- `AttributionWaterfall` bar labels unsigned.
- Vega curve painted red (`charts.tsx:619-624`) though long-vol vega is positive — use a neutral hue.
- Term-structure slope: positive contango painted green — should be neutral; colour only when negative.
- `ConvexityReadout` is a signed read with no colour class (`TenorPanel.tsx:14-33`).
- Sign legend omits the skew meaning ("Skew > 0 = puts rich vs calls").

### 2b. ρ̄ labelling honesty *(old B-06 partial · F-RHOBAR-01)*
Card hint says "hybrid read" but `api.ts:482` `SIGNAL_CAPTIONS` + the "implied correlation" wording
stay misleading vs the realized-vol diagnostic it is (Eq 23, ADR 0051). Candidate: prefix the caption
"Realized-vol diagnostic: …"; thread `member_count`/`basket_size` into the payload as full-membership proof.

### 2c. Coverage / QC provenance
- **F-COV-01:** coverage block dropped when `option_rows == 0` (`analytics.py:361`) → a degenerate-but-real
  capture reads identically to a missing payload. Candidate: always emit coverage + the existing `degenerate` flag.
- **E-02 (OPEN):** Data picker defaults to `available[0]`, no QC-PASS-first sort → can silently land on a
  QC-fail day.
- **E-01 / E-03 (PARTIAL):** no "no-quote-banked" (column-mute) vs "absent" (per-cell —) distinction;
  QC-FAIL badge shown but not actionable ("what failed / which panels").

### 2d. Cross-tab flow
- **A-07 / F-FLOW-01 (OPEN):** the composed basket does not propagate to Risk/Positions (no `BookContext`
  carrier — this is the one retired-branch delta that maps to a real main gap). Candidate: shared context
  or URL param, or a "stress this in Risk Scenarios →" link.
- **A-03 (OPEN):** dual-compose survives (`BuildPriceTab` + `ComposeTab` side-by-side, `Basket.tsx:286-311`).
- **A-04 (OPEN):** empty-book state renders nothing instead of a "add a leg to see your worst case" prompt.
- **F-FLOW-02:** assistant is grounded only on Market; other tabs open to "Choose an index". Candidate:
  each page pushes its `underlying`/`asOf` into `AssistantContext`.
- **F-FLOW-03:** `Guidance/Hotspot` + `PulseHint` are built+tested but imported nowhere — wire on the
  `data-hint="choose-index"` empty state, or delete the dead exports.

### 2e. Stress page (planned, not finished)
- **C-01 (OPEN):** daily stress grid is 9×9, not the planned ±5%/1% crossed 11×11.
- **C-02 (OPEN):** vanna quadrant (spot↓/vol↑) not highlighted.
- **C-03 (PARTIAL):** ±50% tail range exists but not relabelled "crash-test / tail".
- **C-04 (PARTIAL):** 2008/COVID presets present; no term-structure-twist preset.

### 2f. Conformance fine points
- **RM-03 (PARTIAL):** scorecards render *before* the price chart (`Market.tsx:193` vs `218`); blueprint §1
  = price first, context only. Ordering deviation.
- **F-CONF-01:** ProjectionGap is a textual status, not a visual gap on the 3D nappe (§4.5).
- **F-CONF-03:** log-moneyness trusted to be `ln(K/F)` (Eq 6), not `ln(K/S)` — no comment / contract assertion.
- **ATM marker:** no vertical k=0 "ATM" annotation on the smile, though the read is "the gap at ATM".
- **ATM precision label:** ATM IV is a nearest-grid read, not interpolated — hint could say so.

### 2g. Misc polish
- **SV-02 (PARTIAL):** front gated copy is clean but the server `gated.reason` may still carry "3B"
  jargon (`api.ts:652`).
- **D-04 (PARTIAL):** chartTheme/CSS byte-equality test covers positive/negative but not amber/blue.
- Signals tab eyebrow inverts reading order ("Strategy signal layer" before Strategy).
- Strategy `running` has no visible progress indicator (button just disabled).
- "Capture coverage" toggle on Market is unlabelled until expanded.
- `Metric.tsx` renders bare when `hint` omitted — audit call-sites.
- Reference-tenor logic duplicated: front (`scorecards.ts:116`) vs BFF (`grounding.py:186`) — consume the
  BFF `is_reference_tenor` flag.

---

## 3. Open owner rulings (no decision taken)
1. **Convexity in the headline?** §3.2 says niveau/pente/**courbure** are the three-number summary at a
   glance, but convexity was demoted below the smile. Restore as a headline card, or lock the deviation?
2. **No-quote-banked vs absent** (E-01) — confirm the intended semantics before wiring.

---

## 4. Finding provenance
- **Conformance critic** → 2b, 2f (ρ̄ label, convexity §3.2, log-moneyness, ProjectionGap visual).
- **Trading-logic critic** → 2a (vega/slope/skew/convexity colour), ATM marker.
- **UX-fluidity critic** → 2d (basket silo, assistant, Guidance dead), 2g (Signals eyebrow, Strategy run, toggle).
- **Correctness critic** → §0 (run_id, now closed), 2c (coverage block), `Metric` bare values, ref-tenor dup.
- **Verification pass** → §1 landed list + the OPEN/PARTIAL statuses in §2.
