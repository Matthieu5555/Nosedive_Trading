# Spec — Cockpit UX & visual overhaul (post-consolidation)

**Status:** open — **P1** (2026-06-17). **Lane:** `frontend-` (web + a little BFF config). One owner,
sequential batches; each batch lands gate-green before the next.
**Supersedes the ad-hoc review:** this is the formalized, owner-ratified outcome of a multi-agent
UX/design/quant review of the landed 3-onglets cockpit. The 3-tab consolidation itself is done
(`tasks/archive/frontend-3onglets-consolidation.md`); this spec is the quality/identity pass on top.

## Grounding (authoritative — do not re-decide what these settle)
- Reading model & tab structure: `tasks/frontend-3onglets-target-ux.md` (owner-locked) — **amended here**
  for the scorecard set (B) and the "stress is the hero of Risk" hierarchy (A); see those sections.
- Domain authority: `TARGET.md` (§0 scope, §1 money gaps, §3 strategies, R3 ρ̄), `docs/blueprint/`
  (`02-math-framework.md` Eq 23, `05-math-notes.md` §5 scenario families),
  `docs/vol-surface/vol_surface_pedagogique.md` (§3.2 level/slope/curvature, §4.2 term structure,
  §4.5 QC-flag-not-signal), `docs/transcripts/` (architecture-3-onglets, Conseils-front-end,
  Greeks-et-strategies-vol), `.agent/glossary.md`.
- Do **not** revert ADR 0051 (index options + constituent prices only; ρ̄ from realized vol),
  the 3-tab structure, or the all-English UI ruling.

## Owner rulings (A–E) — ratified 2026-06-17

### A. Risk tab → single scrollable page, stress as the hero
Collapse the 4 sub-tabs (`① Composer / ② Le book / ③ Choquer / ④ Attribution`) into **one scrollable
page**: compose → book/$Greeks → **stress (full-width HERO block, the largest thing on the page)** →
attribution. This **realigns the implementation to the locked contract** ("compose → see → shock →
explain, one scrollable tab") — the only owner-ratified addition is the *visual hierarchy* (stress is
the hero). Dissolve the dual-compose smell: one leg grid, layering folded in, pricing implicit (drop
the standalone "Price basket" step). Empty-book state = ghosted hero grid "add a leg to see your worst
case" (direction, not a disabled tab). Live-recompute-on-edit is a **later** increment (stress is
on-demand/no-cron, so it's feasible) — not in this pass.

### B. Scorecard band → 6 cards (amends the locked ⓪)
`ATM level · Term-structure slope · IV-rank · Skew 25Δ · RV−IV · ρ̄`. **Convexity 25Δ is demoted**
out of the headline into the smile block (§3.2 says level/slope/curvature is the *smile* read; the
band is the cross-cutting *book state*). Grounding: §4.2 (contango→backwardation = "signal fort"),
TARGET §3 S1 + R3 (ρ̄ is the dispersion book's thesis → headline, not the bottom strip).
Sign legend to print so the trader never inverts it: **RV−IV > 0 = vol cheap (buy); < 0 = vol rich
(sell); slope < 0 = backwardation = risk imminent.** Define **"vp" = vol point = 0.01 annualized IV**.
Data: 4 of 6 already served by `GET /api/signals` (`by_kind`: `term_structure_slope`, `iv_rank`,
`iv_vs_realized`, `implied_correlation`); ATM/skew already computed in `lib/scorecards.ts`.
**Caveat ρ̄:** today a hybrid implied-index / realized-constituent reading (constituent-option capture
not landed) — label honestly; tighten when constituent IVs land.

### C. Stress grid → two views
- **Daily (new):** crossed grid **spot ±5% / 1% step × vol ±5 vp / 1 vp step** (11×11), with the
  **spot↓/vol↑ vanna quadrant highlighted** (already a cell of the crossed surface — front highlight).
- **Tail (existing):** keep the ±50% / ±50% surface, relabelled "crash-test / tail". Keep the 2008 /
  COVID named presets; add **one term-structure-twist preset**.
Grounding: blueprint §5 (families = spot, vol, **combined spot-vol**, roll; magnitudes owner-tunable),
§3.1 (negative equity skew ⇒ spot↓ ⇒ vol↑ is the real co-move). Vol shocks stay **additive in vol
points** (already the convention). Rate sweep unchanged; correlation shock stays dormant by design.

### D. Visual identity → take the *minimal* bet now (front-only); keep system-sans
- **House "Nappe" colorscale** (replaces stock Plasma on the 3D surface), cold→hot = low→high IV, built
  from the sign palette so the surface speaks the same grammar as every number:
  `[[0,'#1c3a45'],[0.25,'#3f7e93'],[0.5,'#6f8a7c'],[0.72,'#e8c264'],[1,'#f08a7e']]` (ATM band lights
  amber at ~0.72). Keep the 0.35 display ceiling.
- **ATM ridge line:** one amber (`#e8c264`) `Scatter3d` tracing the ATM strike across tenors, appended
  as `data[1]` (never `data[0]` — the Plot mock reads `data[0].z`). The signature gesture.
- **Sign/risk color law:** retune the two existing tokens and **add two new ones** (there is no amber/
  info token today): positive/call `#7fd99a`, negative/put `#f08a7e`, ATM/attention `#e8c264` (new),
  structural/cold `#79b8d6` (new). Every signed number obeys it (scorecards, $Greeks, attribution,
  smile wings). `chartTheme.ts` ↔ `index.css` positive/negative are byte-equality-tested → edit both
  in the same commit.
- **Scorecard band echo:** a whisper-faint left→right gradient (cyan→neutral→amber→coral, ~5% opacity)
  behind `.scorecards`.
- **Deferred:** self-hosted mono for all numbers — only with the full canvas-safe wiring
  (`@font-face font-display:block` + `<link rel=preload>` + gate first chart paint on
  `document.fonts.ready`, wired into BOTH the CSS stack and `CHART_FONT_FAMILY`). This is the exact
  bug that got the previous bundled font removed — do **not** load any webfont without it. Display
  face for hero numbers is staged after the mono proves the pipeline.

### E. Quote provenance / QC (front + ops)
The BFF already serves the per-cell `quote` (bid/ask/volume) — the live "—" is because the displayed
close is **QC FAIL with no banked two-sided quotes** (a QC-PASS close, e.g. 2026-06-15, exists).
- **Front:** distinguish "no quote banked" (column-level muted + a one-line note) from "absent"
  (per-cell "—"); **default the Data picker to the latest QC-PASS close** (not the latest run); make
  the **QC-FAIL badge actionable** (what failed, which panels affected — §4.5 "flag, not signal").
- **Ops (separate, owner-gated):** regenerate a clean close so bid/ask/volume populate. Also: the live
  BFF on :8000 was observed hanging on store-backed endpoints — investigate separately.

### Send-verb ruling
The two distinct send controls (`TicketPanel` "Sign and send order" + `Ordres` "Transmit orders") are
**unified to one verb: "Send to broker"** (the dual label is a fat-finger vector). Both stay disarmed
(3B-gated, paper-only). De-jargon the gated copy: "3B / M2 / disarmed / passage" → "Live sending is
off — paper only".

## Validated build plan (red-teamed) — each batch atomic with its tests, gate-green before the next

Gate per web batch: `npm run lint && npm test && E2E_PORT=<free> npm run e2e` (override the port to
dodge the stale :5173 dev server). Land via `scripts/worktree.sh`; stage by explicit path (never
`git add -A`); rebase between batches (the fleet touches `Basket.tsx`/`Market.tsx`/`index.css`/
`scenarios.yaml`).

**Batch ① — safe quick-wins (charts, format, CSS, color law).**
- Scientific notation → readable fixed/sig-fig formatting at the **call sites** in
  `components/PriceStructure.tsx` and `components/DollarGreeksByMaturity.tsx` (swap `sci`/`sciUnit` for
  `number`/`money`/`signedMoney` already in `lib/format.ts`; raw Greeks span orders → **sig-fig, not
  fixed-2dp**). Do NOT edit `lib/format.ts` itself.
- House Nappe colorscale + colorbar `tickformat:".0%"` + ATM ridge (`data[1]`) in `charts.tsx`;
  `VOL_COLORSCALE` + retuned/new sign tokens in `chartTheme.ts` **and** `--positive/--negative` in
  `index.css` (same commit — byte-equality test). Apply sign-color classes.
- CSS quick-wins: page-header lede `max-width:75ch`; `.market-tabs__list` scroll-snap.
- Tests to change: `pages/Market.test.tsx` (sci-string assertions ~:265-268),
  `DollarGreeksByMaturity.test.tsx` (~:148 railed-row raw cell), `e2e/onglet1-read-flow.spec.ts`
  (~:95-110 mantissa substrings). Verify (don't edit): `charts.robust.test.tsx` (ridge=`data[1]`,
  `z[0].length===4`, `/scatter/` is on SmileChart only), `chartTheme.test.ts` (auto-passes if both
  files edited), `StressSurface.test.tsx` (untouched RdYlGn).
- e2e: `onglet1-read-flow`, `pages`.

**Batch ③ — scorecards 6-card (after ①, independent of ④/⑤).**
- `Scorecards.tsx`: add 3 props + 2 cards, demote convexity into the smile block, sign-color classes,
  print the sign legend. `Market.tsx` (~:128-132): pass `term_structure_slope`/`iv_rank`/
  `implied_correlation` from `signals.data.by_kind`.
- **Extend `src/test/fixtures.ts` `SIGNALS_SX5E`** to add a `term_structure_slope` entry (missing
  today). Update `Market.test.tsx` (~:55-62 — remove the Convexity assertion, add the new cards).
- e2e: `onglet1-read-flow`.

**Batch ② — Ordres rename + single "Send to broker" verb (Ordres scope only; Risk tab labels happen
in ④).**
- `Ordres.tsx` (~:72,145,160 French titles + Transmit button), `pages/ordres/BrokerReconciliation.tsx`
  (~:28), `TicketPanel.tsx` (~:253 visible text + aria-label) → unify to "Send to broker".
- Tests: `Ordres.test.tsx` (~:93-94,116,131), `TicketPanel.test.tsx` (~:83), `Basket.test.tsx`
  (~:210), `e2e/pages.spec.ts` (~:73). e2e: `pages`, `operations`, `navigation`.

**Batch ④+⑤ (merged) — Risk single-scroll + stress hero + BookContext.**
- Rewrite `Basket.tsx`: drop `<Tabs>` (~:258-324), stack sections, fold `BuildPriceTab` pricing into
  the book strip + `ComposeTab` layering into the one leg grid, lift `StressTab` body to the hero,
  remove the duplicate `TicketPanel` (~:249-256; it stays on Ordres). New `BookContext` (greenfield —
  `createContext` with a **default value** so standalone page-mount tests don't throw) carrying
  `{underlying, tradeDate, legs}` across Data/Risk/Orders; Orders hydrates the composed book.
- Delete `pages/basket/BuildPriceTab.tsx` + `pages/basket/ComposeTab.tsx` (+ their tests) — re-cover
  the layer add/move/remove flow in the new page. Rewrite `Basket.tabs.test.tsx` for single-scroll.
- Tests: `Basket.tabs.test.tsx` (rewrite), delete `ComposeTab.test.tsx` + BuildPrice coverage,
  `Basket.test.tsx` (price/tab flow), `e2e/pages.spec.ts` (~:34-88 tab block),
  `e2e/basket-onglet2.spec.ts` (~:227,247 remove tab clicks; section asserts survive). Page-mount
  tests need the BookContext default. e2e: `basket-onglet2`, `pages`, `navigation`, `layout`.

**Batch ⑥ — stress grid daily + vanna + twist (web + Python).**
- `configs/scenarios.yaml`: add the daily ±5%/1% grid block (net-new, **keep** the ±50% as the tail)
  + a term-structure-twist named preset. Engine path for the second grid (`infra .../scenarios`
  `stress_surface`, `apps/frontend/src/.../basket_scenarios.py`). Front vanna-quadrant highlight.
- **Regenerate `packages/infra/tests/golden/risk_pf_risk.json`** (its stamps fold in the scenario
  config hash — `test_determinism_risk.py` ~:99-100). The mocked `e2e/basket-onglet2` grid does NOT
  break. Gate **also** runs `pytest packages/infra apps/frontend`.

**Batch E — QC-PASS default + quote semantics.**
- `Market.tsx` (`AsOfSelect` default to latest QC-PASS, actionable `QcBadge`); `PriceStructure.tsx`
  (column-muted "no quote" vs per-cell "—"). Tests: `Market.test.tsx` as-of/QC. e2e: `onglet1`.

**Deferred (out of this pass):** self-hosted mono/display font; the on-demand basket rate + correlation
engine; live-recompute-on-edit stress; the ops clean-capture; the BFF :8000 hang.

## Acceptance
Each batch lands on `main` gate-green (web lint + vitest + e2e; ⑥ also Python). End state: the cockpit
reads numbers in plain notation, carries one underlying/date/book across the three tabs, makes stress
the visible center of Risk, shows the 6-card instant-read with correct signs, speaks one sign/risk
color grammar with a house vol colorscale, and is all-English with one send verb. No ADR-0051
regression; the 3 top-level onglets unchanged.
