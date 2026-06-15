# Task — Page-1 (Vision marché) build-out to the cahier des charges

**Status:** queued (2026-06-10). **Owner:** unclaimed.
**Goal:** bring Tab-1 up to the block set + reading order described in
[`TARGET.md`](../TARGET.md) §2 (the former `cahier_des_charges_dashboard_page1.md` intent
reference was retired as obsolete — it was an intent example, not a pixel spec; ignore colours).
The base is shipped (commit
`ad97c6c`): control bar + QC badge, index history, constituents row, 3D surface, smile, the
dollar-Greeks term structure. What's missing is the rest of the CDC's §3.3–3.6 and the §2 order.

## Gap (CDC block → current → data today)

| CDC block | Current | Data available now? |
|---|---|---|
| §3.1 control bar + QC badge | ✅ | yes |
| §3.2 index history + [list \| component] row | ✅ | yes |
| §3.3 **vol scorecards** (ATM, 25Δ skew, convexity, realized vol) | ❌ | partial — ATM/skew/convexity from the smile; realized vol from `daily_bar` |
| §3.4 nappe = **heatmap** + 3D, shared colour scale | ⚠️ 3D only | yes (`surface_grid`) |
| §3.5 **2D cuts side by side**: smile + **ATM term structure** | ⚠️ smile in an accordion; term structure absent | yes (`surface_grid`) |
| §3.6 **Greeks = 4 shape cards vs strike** (selected maturity) | ⚠️ greeks vs *maturity*, and empty | ❌ needs **path A** (`projected_analytics` populated) |
| global maturity selector | ❌ | — |

## Phased plan (each phase = a shippable, gate-green increment)

1. **Reading-order reflow + scaffolding** — restructure `Market.tsx` into the CDC §2 order;
   every block a labelled panel with an honest empty/degraded state; lift the smile out of the
   accordion. *(front only; reuses existing hooks)*
2. **Heatmap (§3.4)** — Plotly heatmap of `surface_grid` (maturity × log-moneyness, colour = IV),
   stacked above the 3D, **sharing one value→colour scale** with it. *(data OK today)*
3. **ATM term structure (§3.5)** — 2D ATM-vol vs maturity, side-by-side with the smile, each with
   its own maturity selector. *(data OK today)*
4. **Vol scorecards (§3.3)** + a **global maturity selector** — ATM/25Δ-skew/convexity from the
   smile; realized vol from `daily_bar` returns. *(some BFF compute; explicit rounding)*
5. **Greeks shape cards (§3.6)** — 4 cards of the Greek's shape **along strike** for the selected
   maturity. **Build the shell now; it fills when path A is persisted** (see
   [[vol-surface-front-fallback]]). Distinct from the existing dollar-Greeks term-structure curve.
6. **Design-system pass (§6, optional)** — flat surfaces, thin borders, rounded corners, sentence
   case, two font weights, light/dark via CSS vars; the violet 7-stop ramp **for the nappe only**.

## Notes / constraints

- **Colours are out of scope until phase 6** — the owner ruled the CDC is an intent reference and
  its colours are to be ignored ([[front-page1-design]]).
- Phases **2 and 3 are data-backed today**; **5 stays empty** until path A lands; **4** is partly
  computable now. Surface honest empty/degraded states, never a blank.
- Recommended order: **1 → 2 → 3** as one increment (the bulk of "it finally looks like the CDC",
  all data-backed), then 4, then 5, then 6.

## Phase 7 — robustness to degenerate slices + Greeks table transpose (added 2026-06-15)

The first **live SX5E render** (real 2026-06-15 data) exposed two things the CDC buildout must
absorb (folded in here; the standalone `frontend-page-a-robustness-audit` was merged into this task):

- **Robust to degenerate data.** A single railed/arb-flagged slice (the SVI bound_hit producing
  108%/140% IV spikes — root fix is [[infra-surface-fit-quality]]) currently **blows up the whole
  page from the nappe down**: the 3D Z/colour scale explodes, the greeks panels spike/empty, the
  greeks-by-strike table overflows. The front must **not** depend on that data fix: clamp/flag/exclude
  pathological points (NaN, |IV| absurd, duplicated delta, arb-flagged slice), render the good slices,
  and **mark** the bad one (not silent garbage). Verify by **driving the running app with Playwright +
  screenshot** on a degenerate fixture AND the real store — look at the pixels. Use dedicated
  frontend/UX agents for the component-by-component audit (nappe, dollar-greeks term structure,
  per-maturity, smile, the greeks table).
- **Greeks display = transpose (owner direction).** Re-lay the §3.6 Greeks block as a **table with
  the Greeks as COLUMNS (raw AND currency/devise side by side) and the deltas as ROWS, scrollable,
  grouped/paged by maturity** — instead of the current greeks-vs-maturity panels and the wide
  overflowing strike table. One maturity in view at a time (the global maturity selector from phase 4),
  deltas down the rows, each Greek a raw+devise column pair. This also fixes the table-overflow defect.

Pairs with [[infra-surface-fit-quality]] (the data side — clean slices) and
`frontend-per-side-surfaces-toggle`. P1 once the close-settled data confirms the slices.

### Live-render findings — what landed and what the front CANNOT fix (2026-06-15)

**Landed** (branch `front-page-a-robustness`, render-layer only, no backend change; gate green —
eslint + 125 vitest + tsc): the **Greeks transpose** (Greeks as raw+currency columns, deltas as
rows, maturity selector, scrollable — overflow gone, `scrollWidth == innerWidth`); the nappe Z/colour
**clamped to the sane IV band** (no more page-blowing spike); NaN/absurd/dup points dropped; a
"N slices flagged" note; and **currency from the registry** (`/api/indices` → `€` for SX5E, `$` for
SPX, fallback `$`; no hard-coded `€` — verified `Market.tsx:120` → `IndexAnalytics` → the transpose).

**What it CANNOT fix — confirmed by the close-up screenshots (`docs/_temp/`):** two residual defects
that are **DATA, not front** — both rooted in the **railed short-maturity slices**:
1. **Nappe short-end spike** — a railed short-tenor wing sits at ~0.55 IV, i.e. **inside** the
   `[0, 0.6]` sane band, so the value-clamp cannot tell it from a genuine steep skew and keeps it.
2. **2D Greeks term-structure bunched at the long end** — `charts.tsx bandSeries` excludes points
   whose IV is out-of-band (`!isSaneIv`); the railed short tenors are out-of-band, so they drop and
   only 1y/1.5y survive → every panel collapses to a vertical sliver at the right.

**Blueprint reading:** the front is band-aiding bad data with an **IV-value heuristic**, which the
blueprint rules against (render honestly; fix upstream). The front cannot make railed data clean —
exclude→hidden, show→spike. The correct front signal is the **per-slice QC flag** the backend already
computes (`surface_fit_error`), NOT an IV-value guess — so the BFF should expose the per-slice
flag and the front should grey/flag those slices (and span all maturities in the 2D panels) rather
than drop-by-value. But the **real** fix is the data: upstream QC + the settled-close capture +
the longer-term fallback routing ([[infra-surface-fit-quality]]). **Re-judge the nappe + 2D panels
on settled-close data**, not intraday — on railed intraday data no front layer renders clean.

**Follow-up (front, after the data side):** swap the IV-value heuristic for the BFF-exposed per-slice
QC flag; span all maturities in the 2D term-structure (flag the railed ones, clamp the Y-axis) instead
of excluding them.
