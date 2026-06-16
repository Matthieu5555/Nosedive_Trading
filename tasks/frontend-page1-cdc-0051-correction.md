# Task — Correct the page-1 / market rebuild: restore CDC, conform to ADR 0051, de-leak the run selector

**Status:** open — **PRIORITY / P0** (2026-06-16). Owner ruling: Vincent.
**Lane:** `frontend-` (BFF + web) with one small `infra-`/BFF read change for the day selector.
**Grounding:** [`ADR 0051`](../.agent/decisions/0051-return-to-blueprint-dispersion-realized-vol-diagnostic.md)
(index options + constituent **prices** only; ρ̄ from **realized** vol) · the page-1 CDC
[`frontend-page1-cdc-buildout`](frontend-page1-cdc-buildout.md) · blueprint `15-data-governance.md`,
`19-final-reminders.md:8`, `06-runbooks.md:46`.

> ⚠️ **Coordination.** The market page is Matthieu's active lane (commits `c665614`, `12cfa66`,
> `3411049`, `b10ed3d`). This spec is the **correction list**, not a parallel rewrite — relay to him;
> do not double-edit the same web files concurrently on the shared tree.

## Why

The 2026-06-16 market-page rebuild (`c665614` "rebuild around a market→entity→put/call→maturity
spine") wrapped the on-thesis dispersion view in a **generic market-data browser**. Three defects,
two of which directly contradict the ADR 0051 amputation that landed the same day:

1. **🔴 Re-introduces the constituent-as-option-underlying model ADR 0051 just retired — and it is
   already broken.** `SelectorStrip.tsx:31-33` lets the user pick a **constituent** as the analytics
   "entity" and renders its own surface/smile/Greeks; `DispersionGap.tsx:44-55` (`useBasketAtm`) fans
   out `/api/analytics` **per member** to average their **implied** ATM vol. That is exactly the
   top-10 *implied*-vol bias ADR 0051 removed at the source — and since constituent option chains are
   no longer captured, those calls now resolve **empty**. The page assumes per-member option surfaces
   that no longer exist.
2. **Dropped two CDC blocks that had just landed.** `4138f59` added the nappe **heatmap** (§3.4) and
   **ATM term structure** (§3.5); `c665614` removed both from the mounted page. They survive only as
   dead exports `VolHeatmap` / `AtmTermStructure` in `components/charts.tsx`, imported **only** by
   `components/charts.cdc.test.tsx`.
3. **§3.3 vol scorecards still absent** (ATM / 25Δ skew / convexity / realized vol) — the headline
   missing CDC block; the rebuild spent its budget on the browsing spine instead.

Plus a **serving-layer regression** (`b10ed3d` + `12cfa66`): the run-partition leaked into the UI.
`/api/recorded-dates` (`recorded_dates.py:67-82`) emits **one entry per capture run**, so the day
selector lists every intraday fetch as a peer as-of — including dry-run/test fires. The blueprint
mandates **one canonical close per `trade_date`** in the serving view (replay history via an explicit
`version=`, never as the default day picker). RAW is untouched and stays append-only — correct; the
leak is purely serving-layer.

## Scope of change — the correction checklist

1. **Day selector → one canonical close per `trade_date`.** `/api/recorded-dates`
   (`recorded_dates.py:67-82`) emits **one** entry per date — the newest run (= what a default read
   already returns). Drop the per-run fan-out; `marketHeader.tsx:58-88` then shows one row per day.
   Keep run partitions on disk for forensic replay, reachable only behind an explicit
   `version=`/run_id affordance, **off** the main picker. (Re-fetch ⇒ the day shows once, latest wins
   — the owner's expectation.)
2. **Remove the constituent-as-underlying axis.** Drop the constituent "entity" picker
   (`SelectorStrip.tsx:31-37`); the page is **index-keyed** (SX5E). No per-member surface/smile/Greeks
   route (`/api/analytics?underlying=<member>` must never be called for a constituent).
3. **Rewire the dispersion gap to realized-vol ρ̄.** `DispersionGap` must read the persisted
   **realized-vol** dispersion/ρ̄ signal from the BFF (`strategy_signals.implied_correlation`, index,
   as-of) — **not** recompute mean member *implied* ATM vol via a per-member `/api/analytics` fan-out
   (`DispersionGap.tsx:44-55`). If no BFF ρ̄ endpoint exists, add a thin read of `strategy_signals`
   for the index as-of (one call, no member fan-out). Aligns the front with the amputation.
4. **Restore the nappe heatmap (§3.4) + ATM term structure (§3.5)** onto the page — `VolHeatmap` /
   `AtmTermStructure` are already built and tested (`charts.tsx`); re-mount them in the index
   analytics stack per CDC reading order, sharing the 3D's colour scale (the nappe = heatmap+3D, one
   scale).
5. **Add the §3.3 vol scorecards** (ATM / 25Δ skew / convexity / realized vol), index-keyed, as the
   CDC headline row.
6. **Default the as-of control to the settled close**, not a per-fetch list. Reconsider the global
   **put/call** axis: the smile is read **whole** (both wings = the skew); a default put view is
   defensible for the book thesis but not a hard global market-data filter that fragments the object.

## Acceptance

- No `/api/analytics` call is made for a constituent symbol; the page renders index-keyed only
  (grep guard / test). ρ̄ comes from `strategy_signals` (realized), front shows a real value, not an
  empty member fan-out.
- The day selector lists **one** row per `trade_date`; a same-day re-fetch does not add a row.
- Nappe heatmap + ATM term structure + §3.3 scorecards are mounted and data-backed; the
  `charts.cdc.test.tsx` exports are no longer orphaned.
- Web gate green (lint + vitest + tsc); BFF tests for the collapsed `/api/recorded-dates`.

## Out of scope

Forensic replay UX (the explicit `version=`/run_id history affordance) is a separate, later enhancement.
Reconvert-to-constituent-**price** coverage on the coverage panel is tracked with the archived
amputation spec.
