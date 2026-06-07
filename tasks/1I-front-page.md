# 1I — Front page & API: pick index → constituents → ticker → chart + analytics, wired to the real pipeline

- **Owns:** the operator front page and the BFF endpoints behind it. On the Python side
  (`apps/frontend/src/algotrading/frontend/`): a new per-ticker **DailyBar OHLC price-history**
  router, a new **constituent-list** router over 1A membership, a new **projected-analytics**
  router (surface grid + dollar Greeks) over 1F, and a **recorded-dates** router (capture-coverage
  counter + date list) over the 1G run ledger — plus their serializers and registration in
  `app.py`. On the web side (`apps/frontend/web/src/`): the real Home/front page (`pages/Home.tsx`
  is static nav today), the chart + surface + smile + accordion + dollar-Greek components, the
  `api.ts` typed clients, and the front-stack dependencies. Conforms to
  **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)** (blueprint governs the
  domain — the $-Greek metric contract and field names are its call, not ours),
  **[ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)** (Plotly.js
  for every chart; shadcn/ui + TanStack Table for the shell), and **[ADR 0029](../.agent/decisions/0029-contract-field-names-conform-to-blueprint.md)**
  (the OQ-7 field names: `forward_price`/`implied_vol`/`log_moneyness`/`dollar_*`).
- **Depends on:** 1A (`tasks/1A-universe-membership.md`) for the as-of constituent list; 1C for the
  `DailyBar` OHLC contract + its captured history (the price chart has nothing to read without it);
  1F for the projected (tenor × delta-band) analytics grid + the dollar Greeks; P0.2 for the
  $-unit contract (every dollar number carries an explicit unit string). This is the **last** step of
  Phase 1 — the data pipeline must exist first.
- **Blocks:** nothing in Phase 1; it is the leaf. Tab 2 (2A basket builder) reuses these components
  and BFF seams, so build them clean.
- **State going in (verified 2026-06-07):** the BFF (`app.py`) wires six real routers —
  `health`, `surfaces`, `risk`, `run`, `config`, `oauth` — each reading `packages/infra` seams
  (`ParquetStore`, the pure engines) and serializing; no business logic in routers. The
  `market`/`orders` paper-trading routers **and `store_serving.py` were deleted in C4** (≈700 lines
  of fixtures, no backend equivalent) — do **not** cite `/api/market` or `store_serving.py` as
  existing. `pages/Home.tsx` is a static `<Link>` list; `Risk`/`Surfaces`/`Config`/`Health` pages
  render real BFF data through `getJson`/`useFetch`/`AsyncBlock`. The web app has **no** chart
  dependency yet (`package.json` has no plotly/shadcn/tanstack). The `DailyBar` and
  `IndexConstituent` contracts do **not** exist yet (1C/1A own them); `PricingResult` today carries
  `dollar_delta`/`dollar_gamma`/`dollar_vega` only (no `dollar_theta`/`dollar_rho`) and
  `RiskAggregate` is decimal-only — the full dollar set arrives via 1F/P0.2.

## Objective

An operator opens the front page, picks the index, scrolls its **point-in-time** constituent list,
selects a ticker, and sees — laid out **price-first** — that ticker's **real daily OHLC chart**
beside its **3D vol surface**, a **2D smile (vol vs delta) per maturity** in an **accordion**, and
its **dollar Greeks** with explicit unit strings. Every panel self-labels (answers *"what am I
looking at?"*). Every number traces to the real pipeline through a store-backed BFF endpoint — no
fixtures, no mocks. The three new BFF endpoints are "the API" people refer to; there is no separate
API task.

## What to do (ordered)

1. **DailyBar OHLC history endpoint (BFF).** Add `routers/price_history.py`
   (`GET /api/price-history?underlying=&start=&end=`) that reads the `DailyBar` table (1C/1E) from
   `ParquetStore` for one ticker over a window and serializes one row per day:
   `trade_date, open, high, low, close, volume` + provenance. Empty/missing partition → an empty
   `bars` list with the labels, never a 500 (match the `surfaces` router's missing-partition
   behaviour). Add a `daily_bar_to_dict` serializer beside the others; do **not** invent OHLC field
   names — use the `DailyBar` contract's fields as 1C froze them.
2. **Constituent-list endpoint (BFF).** Add `routers/constituents.py`
   (`GET /api/constituents?index=&as_of=`) that calls 1A's as-of resolver
   (`members(index, as_of_date)`) and returns the historical basket — `instrument_key`/symbol,
   weight, `effective_add_date`/`effective_remove_date` — ordered **price-first** (the roadmap's
   ordering rule; sort by the latest close from the DailyBar table, names without a bar last). The
   resolver is the look-ahead gate: pass `as_of` straight through; never default it to "today" then
   apply it to a past date.
3. **Projected-analytics endpoint (BFF).** Add `routers/analytics.py`
   (`GET /api/analytics?underlying=&trade_date=`) that reads 1F's projected (tenor × delta-band)
   grid + the dollar Greeks back from the store and serializes, per maturity: the SVI slice (reuse
   `surface_parameters_to_dict`), the smile points (vol vs delta across the 30Δ-put→ATM→30Δ-call
   band), the surface grid cells for the 3D trace, and the dollar Greeks **each tagged with its
   P0.2 unit string** (Delta\$ per \$1, Gamma\$ per 1% move, Vega\$ per vol point, Theta\$ per
   calendar day, Rho\$ per 1% rate). Field names follow ADR 0029 (`forward_price`, `implied_vol`,
   `log_moneyness`, `dollar_*`) — the unit strings come from 1F/P0.2, not invented here.
4. **Register the new routers** in `app.py` alongside the existing six (CORS already covers GET) —
   `price-history`, `constituents`, `analytics`, plus the `recorded-dates` router (step 8).
5. **Front-stack dependencies (web).** Per ADR 0030 add `plotly.js` (the single charting dep:
   `candlestick`/`ohlc`, `scatter`/`line`, `surface`/`mesh3d`), shadcn/ui (+ Radix + Tailwind) for
   the shell, and `@tanstack/react-table` for the dense grid. Do **not** add TradingView Lightweight
   Charts — it is a documented fallback only, adopted later iff Plotly's candlestick is too janky for
   daily bars.
6. **Typed clients (web).** Extend `api.ts` with `PriceHistoryResponse`/`DailyBar`,
   `ConstituentsResponse`/`Constituent`, and `AnalyticsResponse` interfaces mirroring the new
   serializers (the comment at the top of `api.ts` makes the HTTP shape the seam — keep them in
   sync). Reuse the existing `getJson` helper and `useFetch`/`AsyncBlock`.
7. **The front page (web).** Replace the static `pages/Home.tsx` with the real page: an index picker
   → a **scrollable** constituent list (TanStack Table, price-first) → on ticker select, a
   **price-first** detail layout: the **candlestick** chart (Plotly `candlestick` trace from
   `/api/price-history`) at the top, then the **3D IV surface** (Plotly `surface`/`mesh3d`), then an
   **accordion per maturity** (shadcn) each holding the **2D smile** (Plotly `scatter`, vol vs
   delta) and that maturity's **dollar Greeks with their unit strings**. Every panel carries a
   self-describing label. A line chart is an acceptable candlestick fallback only if OHLC is absent.
8. **Recorded-dates counter + date picker.** BFF: add `routers/recorded_dates.py`
   (`GET /api/recorded-dates?index=`) returning, for the chosen index, the list of `trade_date`s with a
   **completed, gap-free** capture run **plus the count** — sourced from the **1G run-state ledger**
   (which distinguishes complete from partial), not a raw partition listing. Front: a **"N days
   recorded"** counter and a **date dropdown** that drives the page's `as_of`; selecting a past date
   **re-resolves** the constituent list and analytics as-of that date (1A) — never default `as_of` to
   today. Empty / not-yet-captured → a labeled empty state with count 0, never a 500.

## Test surface

Read `tasks/TESTING.md`. The BFF is covered by the root Python gate; the web app by
`apps/frontend/web`'s `npm run lint && npm test`. Specific named cases:

- **BFF↔infra seam (extend `apps/frontend/tests/test_readback_api.py`, the pinned BFF↔infra seam):**
  seed real `DailyBar`, `IndexConstituent`, and projected-analytics rows through `ParquetStore.write`
  (hand-chosen, internally-consistent values, derived independently of BFF output) and assert each
  new router reads **those** values back unchanged with their provenance — `test_price_history_reads_back_daily_bars`,
  `test_constituents_reads_back_as_of_basket`, `test_analytics_reads_back_surface_and_dollar_greeks`.
- **Field-name conformance:** assert the price-history payload exposes the `DailyBar` OHLC fields and
  the analytics payload uses `forward_price`/`implied_vol`/`log_moneyness`/`dollar_*` (ADR 0029) —
  a renamed contract field must turn the assertion red (`test_analytics_payload_uses_blueprint_field_names`).
- **Dollar-Greek unit contract (P0.2):** every dollar number in the analytics payload carries a
  non-empty unit string with the pinned semantics — `test_dollar_greeks_carry_unit_strings`.
- **No look-ahead:** an `as_of` in the past returns the basket in force **then**, not today's; a
  member added after `as_of` is absent and one removed before it is absent
  (`test_constituents_as_of_excludes_future_members`). Run the `check-lookahead-bias` skill over the
  constituent + price-first-ordering join.
- **Recorded-dates reflects only complete runs:** seed two gap-free completed runs + one partial/failed
  run in the ledger; `/api/recorded-dates` returns exactly the two dates with `count == 2`
  (`test_recorded_dates_excludes_incomplete_runs`); picking a returned past date drives the as-of
  re-resolution (`test_recorded_date_pick_reresolves_membership_as_of`).
- **Missing-partition / empty / boundary:** an unknown ticker → empty `bars`/empty analytics with
  labels and HTTP 200, never a 500 (`test_price_history_unknown_ticker_is_empty_not_500`); a
  malformed `trade_date`/`as_of` → a labeled 400 (mirror the `surfaces` router's `bad_trade_date`).
- **Web component tests (Vitest + Testing Library, alongside `Surfaces.test.tsx`/`Risk.test.tsx`):**
  the constituent list renders and is scrollable; selecting a ticker renders the candlestick, the 3D
  surface, the per-maturity accordion + smile, and the dollar Greeks **with their unit strings
  visible**; every panel renders its self-label; a fetch error renders through `AsyncBlock`, not a
  blank page (`Home.test.tsx`). Assert user-facing text/labels per the write-tests UI rule (mock the
  three endpoints; do not hit a live BFF).

## Done criteria

The four new store-backed BFF routers (`price-history`, `constituents`, `analytics`, `recorded-dates`) are registered
and read the real `DailyBar`/`IndexConstituent`/projected-analytics tables back through `ParquetStore`
— no fixtures, no `store_serving.py`, no `/api/market`. The front page lets an operator pick the
index, scroll the point-in-time constituent list, select a ticker, and see its real daily candlestick
beside the 3D surface, the per-maturity accordion + smile, and the dollar Greeks — every dollar
number unit-tagged (P0.2), every panel self-labelled, ordered price-first. Field names conform to
ADR 0029. The web stack is Plotly + shadcn/ui + TanStack Table per ADR 0030. Both gates green:
`npm run lint && npm test` in `apps/frontend/web`, and the root Python gate (`ruff && mypy &&
lint-imports && pytest`) covering the BFF.

## Gotchas

- **Serving is read-only; the cron is the sole writer ([ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md) §1, [0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)).**
  The BFF opens `ParquetStore` / DuckDB **read-only**; only the EOD cron (1G) writes. Point-in-time
  reads (constituents as-of, price-first ordering, snapshot↔bar alignment) resolve through 1A's
  resolver / DuckDB's native **`ASOF JOIN`** over the Parquet store — never a hand-rolled merge or a
  today-defaulted lookup. That is the look-ahead gate at the query layer.
- **Do not resurrect the deleted code.** `/api/market`, `/api/orders`, and `store_serving.py` were
  removed in C4 because they were 100% fixtures. The new endpoints read the store; if a table you
  need is empty, return labeled-empty, do not synthesize.
- **This is the leaf — it has nothing to read until 1A/1C/1F land.** `DailyBar` and
  `IndexConstituent` do not exist yet and the dollar Greeks are incomplete on today's contracts
  (`PricingResult` has no `dollar_theta`/`dollar_rho`; `RiskAggregate` is decimal-only). Sequence
  after those; do not stub the contracts here to unblock yourself.
- **The blueprint (ADR 0011) overrides on the metric contract.** The $-Greek formulas, units, and
  the `dollar_*`/`forward_price`/`implied_vol`/`log_moneyness` names are the blueprint's / P0.2's /
  ADR 0029's calls. The BFF serializes and tags units; it does not redefine them or recompute Greeks.
- **The HTTP shape is the seam** (the `api.ts` header comment). A serializer change without the
  matching `api.ts` change is a silent drift — the seam test in `test_readback_api.py` is what keeps
  it honest.
- **Price-first ordering needs the DailyBar close**, which lives in a different table from membership
  — join carefully and put names without a bar last; do not let the join leak a future close into a
  past `as_of` view (look-ahead).
- **Recorded-dates source of truth is the 1G run ledger, not partition listing.** Partitions can exist
  for a partially-captured or failed day; the counter/dropdown must show only complete, gap-free runs
  (it is the operator-facing face of 1H coverage). A picked date re-resolves membership/analytics
  as-of — never today-defaulted.
- **uv** for the Python BFF; **npm** for the web app. Plotly only — no second charting dependency;
  TradingView stays a documented fallback, not a dependency.
