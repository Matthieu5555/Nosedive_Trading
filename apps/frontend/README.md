# apps/frontend

The operator frontend: a FastAPI backend-for-frontend (BFF) plus a React/Vite web
app. Top of the layer stack — it reads only *down* into `packages/infra`, never up into
`strategy`/`execution` (import-linter enforces this). Owner: **M8**.

## TL;DR

The BFF is the only place infra meets HTTP. Its routers read the real
`packages/infra` seams — `ParquetStore` for the persisted contract tables, the pure
`surfaces`/`risk` engines, the as-of `universe.members` resolver, the `run_state` ledger,
and `orchestration.build_dashboard` — and serialize the result to JSON-primitive payloads.
No business logic lives in the routers; they call infra and serialize, and surface errors
as typed payloads rather than 500s. The store opens read-only — only the EOD cron writes
(ADR 0034 §1). The web app is the only consumer above this layer.

Run the BFF:

```
uv run uvicorn algotrading.frontend.app:app --reload --host 127.0.0.1 --port 8000
```

Run the web app:

```
cd apps/frontend/web
npm install
npm run dev
```

The Vite dev server proxies `/api` and `/healthz` to `127.0.0.1:8000`, so the web app
and API share an origin in development (no CORS dance); `FRONTEND_BASE_URL` covers
production CORS.

## Tests

Two layers, both under `apps/frontend/web`:

- **Component tests** (`npm test`, Vitest + Testing Library + MSW, jsdom) — the verification
  gate, alongside `npm run lint`. They cover render, data fetching and handlers per component.
- **End-to-end tests** (`npm run e2e`, Playwright, real Chromium) — what jsdom cannot do:
  navigation/button flows across routes and **layout-collision / overflow** checks (elements
  don't overlap, controls stay on-screen, no horizontal overflow) at desktop, laptop and narrow
  viewports. Specs live in `web/e2e/`; the BFF is mocked at the network layer with the same
  contract fixtures the component tests use, so the suite is deterministic and never touches a
  live BFF or the canonical data store. Playwright boots the Vite dev server itself.

  E2E is **opt-in**, not part of `npm test`: it needs a browser binary
  (`npx playwright install chromium`, one-time ~110 MB) and a running dev server, so wiring it
  into the shared gate is a team decision. Run it locally with `npm run e2e`
  (`npm run e2e:ui` for the inspector, `npm run e2e:report` for the last HTML report).

### The never-blank three-state policy

Every async surface must render something legible in all three states, and the three must read
differently: **loading** is a `role="status"` skeleton that reserves the panel footprint (never the
bare text "Loading…"); **empty** is an affirmative sentence that names its subject; **error** is a
loud `role="alert"` with a recovery path. `AsyncBlock` carries the loading/error halves for the
whole app — its loading branch mounts the footprint-preserving `<ChartSkeleton>` (`components/
Skeleton.tsx`) after a `SKELETON_DELAY_MS` (1 s) floor, so a sub-second fetch shows no loader (P4)
while a longer one fades the chart in with zero reflow. New async surfaces use the
`assertNeverBlank(renderResult)` helper (`src/test/assertNeverBlank.ts`) in their component test to
guarantee the surface is never a silent blank.

## Pages

Three top-level onglets over `react-router`, wrapped in the shared top-bar shell: **Données →
Risque → Ordres** (`frontend-3onglets-target-ux.md`, owner-locked). Each onglet is one row in
`src/routes.ts` (`ROUTES`) and one entry in the `PAGES` map in `src/App.tsx`; the nav and the route
table both render from `ROUTES`. **Operations** is a secondary utility (a quiet topbar link to
`/operations`), not a top-level onglet. The 7-tab era collapsed here: Risque absorbs Basket + Risk
Scenarios + Positions; Ordres absorbs Orders + Strategy; Signals was dropped (its content lives in
the Données scorecards + ρ̄ strip). Legacy paths (`/market`, `/basket`, `/risk`, `/positions`,
`/orders`, `/strategy`, `/signals`) redirect to their new home.

- **Données** (`/`, `src/pages/Market.tsx`) — the index-analytics reading page, INDEX-KEYED (ADR
  0051): a scorecard strip (ATM · skew · convexity · RV−IV), the price block (index candlestick +
  the master-detail constituents — weighted list + the selected member's candlestick), the 3D vol
  nappe, one tenor selector driving the put/call smile + the per-strike price structure
  (bid/ask/volume) + the Greeks (profile curves + magnitude table), and the ρ̄ dispersion strip.
- **Risque** (`/risque`, `src/pages/Basket.tsx`) — compose → see → shock → explain, over a shared
  composer and four sub-tabs: **① Composer**, **② Le book** (the booked book folded in from the
  former Positions page), **③ Choquer** (on-demand stress + the named historical / persisted
  scenarios folded in from Risk Scenarios), **④ Attribution** (by-Greek waterfall).
- **Ordres** (`/ordres`, `src/pages/Ordres.tsx`) — the order home: the ticket (gated; live transmit
  is 3B-gated, the send button disarmed, commit paper-only behind the password barrier), the broker
  reconciliation (moved here from Risk), and the folded backtest (the former Strategy page).
- **Operations** (`/operations`, `src/pages/Operations.tsx`) — the operator dashboard (system
  health, run control, freshness), kept as a secondary utility rather than a product onglet.

## API

The BFF exposes (all under `/api` except the liveness probe):

- `GET /healthz` — liveness (no infra reads).
- `GET /api/health[?trade_date=YYYY-MM-DD]` — operator dashboard status.
- `GET /api/surfaces[?underlying=&trade_date=]`, `GET /api/surfaces/underlyings`.
- `GET /api/risk[?portfolio_id=]`, `GET /api/risk/portfolios`,
  `GET /api/risk/scenarios[?portfolio_id=]`. Beside the parametric `surface` and the raw `cells`,
  the scenarios payload carries an additive `named` list (`n_named`): the **named historical
  scenarios** (`scenario_id` `named_<label>`) bucketed per scenario, each a labelled compound shock
  (`spot_shock`/`vol_shock`/`rate_shock`) with its book-summed `scenario_pnl` and `n_legs`. Empty
  list on an unconfigured / parametric-only grid, so the surface contract stays byte-identical when
  there are no named scenarios. It also carries an additive `rate` sweep (`n_rate`): the engine's
  **rate-shock family** (`scenario_id` `rate_<±shock>`, the additive forward-fixed parallel rate
  sweep — *not* crossed with the spot×vol surface, owner-ruled) bucketed per shock, each labelled
  with its `rate_shock` (fraction), `bp` (basis points), book-summed `scenario_pnl` and `n_legs`,
  sorted ascending by shock. The BFF serializes the banked `rate_` valuations — it never re-shocks.
  Empty list when the scenario grid configures no `rate_shocks` (byte-identical surface contract);
  the web Risk Scenarios page renders the sweep as its own `RateSweep` panel only when present, so
  an unconfigured grid renders exactly as before. The **on-demand basket** stress
  (`/api/basket/scenarios`) carries no rate sweep yet — its engine reprices spot×vol only; a basket
  rate sweep is a follow-up (it needs the basket stress engine to emit a rate family). The
  correlation family stays dormant (a ρ̄ bump reprices to zero on the live option book until a real
  `BasketCorrelationExposure` lands — `frontend-named-scenarios-wiring`).
- `POST /api/basket/risk` — price/risk a composed multi-leg basket as the book-additive sum of
  its legs' stored dollar Greeks (WS 2A; summation, never a reprice).
- `POST /api/basket/scenarios` — the **on-demand** full-reprice stress surface for a composed
  basket (WS 2B): reconstructs a valuation per option leg from the stored grid and reprices over
  the config-driven (spot × vol) grid, returning the same `surface` shape as
  `/api/risk/scenarios` plus the worst-case cell and labelled per-leg gaps. The interactive,
  no-cron counterpart to the persisted-surface read — works off today's analytics without a
  configured portfolio.
- `GET /api/providers`, `GET /api/run/underlyings`, `POST /api/run`,
  `GET /api/jobs`, `GET /api/jobs/{id}` — the job payload carries additive-nullable
  `stage` (PM-register label) / `stage_index` / `stage_total` for determinate progress
  narration (see "The live-run build path").
- `GET /api/config`, `GET /api/config/{filename}`.
- `GET /api/config/delta-bands` — the ordered delta-band axis (`30dp … atm, atmp … 30dc`) the
  basket leg selector offers, the single source built from `qc_threshold.grid` via
  `ProjectionConfig.from_band` (no hard-coded band list on the front); falls back to the default
  axis when no config bundle is loadable.
- `GET /api/price-history[?underlying=&start=&end=]` — daily OHLC bars for one ticker over a
  window, from the `daily_bar` table (WS 1I).
- `GET|POST /api/price-history/batch` — grouped daily OHLC histories for a requested list of
  underlyings. The front uses `POST` with `underlyings[]` and `end=<as_of>` so the first page has
  all constituent histories without one browser request per ticker.
- `GET /api/constituents[?index=&as_of=]` — the point-in-time index basket via the as-of
  `members` resolver (the no-look-ahead gate), from `index_constituents`; the web app orders it
  by **index weight** (market-cap proxy) and default-selects the heaviest name (WS 1I).
- `GET /api/analytics[?underlying=&trade_date=]` — the projected tenor × delta-band grid
  (smile + surface slice + dollar Greeks with unit strings) from `projected_option_analytics`
  (WS 1I). **Index-keyed:** the option chain is captured at the index level, so the web app
  queries this with the *index* symbol (the vol surface is the index's), not the selected
  constituent — the constituent selection only drives its price candlestick. The smile's
  x-axis declares itself via `axis_type` (F-BFF-04): `"delta"` + `deltas` on the rich
  projection, `"moneyness"` + `moneyness_buckets` on the surface-grid fallback — bucket
  values are never relabelled as deltas. Each `surface_slice` carries the full fit
  diagnostics (`bound_hits`/`converged` beside `rmse`/`n_points`/`arb_free`) plus the
  derived `degenerate`/`degenerate_reasons` flag, so a railed SVI calibration renders
  flagged, never as clean. It also carries two additive-nullable fields the legibility theme
  needs: `close_instant` — the option settlement close as a PM-legible venue time-of-day + zone
  (`"17:30 CEST"`, resolved from the index registry's calendar + `option_settlement_close`, honest
  per-date, never a hard-coded `"CET"`; null off-registry), and `coverage` — the one shared
  `{option_rows, two_sided, excluded, two_sided_fraction}` block (computed once in
  `grounding.coverage_from_snapshots`, shared with the assistant frame; null when no option rows),
  so the nappe caption can state *built-on / captured* without re-deriving the metric. The stress
  surface (`GET /api/risk/scenarios`) labels missing `(spot, vol)` cells as `null` holes with
  `has_holes`/`n_holes` (F-BFF-03), never `0.0`.
- `GET /api/recorded-dates[?index=]` — from the 1G run-state ledger. Returns `dates`/`count`
  (the **qc-clean, gap-free** days — the operator coverage figure) **and** `available`: every
  **viewable** day (whose `analytics` stage produced a surface, **including qc-failing ones**),
  each tagged with its QC verdict (`pass`/`fail`/`unknown`). The date picker offers `available`
  and shows a QC badge, so a degraded snapshot is shown rather than hidden (WS 1I).
- `GET /api/attribution[?trade_date=&portfolio_id=&level=&contract_key=]` — the by-Greek P&L
  decomposition for one persisted `scenario_attributions` record (TARGET §2 #5 / §7 #2). Projects
  the frozen `ScenarioAttribution` seam **verbatim** (the BFF re-decomposes nothing): `terms` are
  the per-Greek dollar contributions in the ADR-0030 dPnL order (Δ → Γ → Vega → Θ; Rho/Vanna/Volga
  appended by the second-order-greeks lane as the seam grows), each a labelled `{name, dollars,
  unit}`; `residual` is the honesty meter against the full reprice carried as its **own** bar
  (never folded into a term); `verdict` is the engine's `within_tolerance` ruling against its
  echoed `residual_abs_tol`/`residual_rel_tol`. `level=book` (default, the book sentinel
  `contract_key`) or `level=position` + a `contract_key` for the §5.8 per-position drill. No
  record for the `(portfolio, date)` is a labelled-empty `found=false` body (HTTP 200), a bad
  `trade_date` a labelled `400`. The web `AttributionWaterfall` (Plotly waterfall) renders it on
  the Basket Builder's **Attribution** tab, which carries its own portfolio input. The Basket
  Builder splits its work into three sub-views over the shared `@/ui/tabs` (the same component the
  Market page uses): **Build & price** (price the basket → `BasketRiskPanel`), **Stress** (the
  on-demand `StressSurface`), and **Attribution** — all over one shared leg composer
  (underlying / trade date / tenor / templates / leg grid / order ticket) that stays above the tabs.
- `POST /api/backtest/run` — launch a store-backed S2 backtest over the offline store and return
  the full result in one call (TARGET §7.8 / §5.7; F-STRAT Strategy/Backtest page consumes it). No
  persisted backtest table — it runs on demand through the landed research engine
  (`algotrading.strategy.backtest`) driven by `StoreBackedBacktestData`, reinventing no compute.
  Body: `index`, `reference_tenor`, `start_date`/`end_date` (narrowed to the days actually banked
  for the index — none banked → labelled `400 no_banked_days`), `provider`, a `put_line` config
  block (an invalid one → `400 bad_put_line_config`), optional `costs`
  (`commission_per_contract`/`slippage_rate`) and `stress_grid`. Response: `summary` (gross +
  **net** P&L, **total transaction cost**, max drawdown, Sharpe, turnover, worst stress),
  `cumulative_attribution` (the named per-Greek "which Greek paid" view), and a `days[]` array
  (per-day open contracts, realized + net P&L, transaction cost, stress loss, exposure Greeks). An
  inverted window is a labelled `400 bad_window`.
- `GET /api/coverage[?underlying=&trade_date=]` — the captured option chain as a plain quality
  table (no recompute), rendered by the web `CoverageTable`/`CoveragePanel`. Three already-on-disk
  facts: **per-expiry capture** (strikes/calls/puts/span from `instrument_master`), **per-tenor
  coverage** over the whole pinned grid (from `qc_results` `tenor_coverage_floor`, so an empty
  tenor shows as a labelled zero-row), and — for an index underlying — the **per-constituent
  capture-outcome ledger** from `constituent_capture_outcomes`: each of the index's heaviest names
  with its labelled verdict (`captured`/`no_options`/`unentitled`/`unresolved`), heaviest-first, so
  the entitlement question (*which* names return chains on this account) is visible per name rather
  than a silent absence. A missing partition is a labelled-empty payload (`n_expiries == 0`,
  `constituents == []`, HTTP 200); a bad `trade_date` a `400`.
- `GET /api/signals[?underlying=&trade_date=&run_id=]`, `GET /api/signals/underlyings` — the
  persisted **signal layer** (`strategy_signals`, layer `signals`) read back per index and as-of,
  rendered by the web **Signals** page (F-SIG). Read-only over what the EOD cron banked — the BFF
  **recomputes no signal math**. Each row is the serialized `StrategySignal` (`signal_kind`,
  `subject`, `tenor_label`, `value`, `snapshot_ts`, `source_snapshot_ts`, full `provenance`) plus a
  display `label`/`unit` keyed off the kind: `iv_rank` (IV rank, `fraction [0,1]`), `iv_vs_realized`
  (Realized − implied, `vol points (annualized)`), `term_structure_slope` (Term-structure slope,
  `vol points (back − front)`), `implied_correlation` (ρ̄, `correlation [-1,1]`). The payload carries
  the flat `signals` list **and** a `by_kind` index (so F-SIG keys off kind without re-grouping) plus
  the `kinds` order. `underlying` is the *index* (the `underlying` column); `subject` is the name the
  reading is about (index or constituent). `trade_date` absent resolves the latest persisted
  partition; `run_id` pins one fetch. A missing partition is a labelled-empty body (`n_signals == 0`,
  `by_kind == {}`, `snapshot_ts: null`, HTTP 200); a bad `trade_date` a `400`. **Not surfaced:** IV
  *percentile* — `iv_percentile` exists in `infra/signals` but the layer persists only `iv_rank`, and
  this read-only slice will not recompute it (it flows through unchanged once the layer banks it).
- `GET /api/positions/fills[?trade_date=&underlying=]` — the append-only **fills ledger** read
  back verbatim from `<store_root>/booking/fills.jsonl` (the file the password-gated booking commit
  writes). Each fill carries its signed `signed_qty` (a string so the `Decimal` survives JSON),
  paper `mode`, the venue-stamped `fill_ts`, and lineage (`booking_id`/`source_basket_id`/
  `broker_contract_id`). No recompute — this is the §6 source of record. Empty when nothing is
  booked (HTTP 200).
- `GET /api/positions[?trade_date=&underlying=]` — the **booked position set** the book is
  accounted *from fills, never from intentions*: the ledger folded by `contract_key` (partial fills
  accumulate, a net-zero leg is closed and absent) into one line per live contract, each joined to
  the latest banked `pricing_results` row for that key to carry per-leg Greeks (`raw` per-unit,
  `position` = `raw × signed_qty × multiplier`, `dollar` = banked dollar-Greek × `signed_qty`, each
  with its unit) plus `mark_price`/`market_value`. The `book` block is the **additive** sum of the
  dollar Greeks and market value across priced legs. A booked leg with no banked pricing is a
  labelled `unpriced_contract_keys` entry (zeroed Greeks, never silently dropped), and
  `priced_contract_keys` counts the rest. The web Positions/Execution blotter (F-POS) consumes
  these two endpoints. The store opens read-only; nothing here writes a fill or touches a broker.
- `GET /api/reconciliation[?account_id=]` — the **broker-account reconciliation** for the
  Operations / Risk recon view: it reads the latest banked broker snapshot per account
  (`broker_positions`/`broker_cash_balances`/`broker_fills`, picked by the most recent `as_of_ts`)
  plus the fills ledger, and runs `infra.risk.reconcile_account` to diff the broker's account state
  against the fills-based book. The body carries an `ok` flag and three sections (`positions`,
  `cash`, `fills`), each with `counts` (`match`/`break`/`broker_only`/`book_only`) and per-line
  detail. Positions/fills join on the broker conid (`str(conid)` ↔ the book `broker_contract_id`),
  signed-quantity diffed against a versioned tolerance; cash is informational broker-only (the
  fills-book carries no cash leg). No recompute beyond the diff. `account_id` absent resolves the
  account on the latest broker positions; **no broker positions captured** is a `400`
  (`no_broker_account`). Margin forecasting, kill switch, and recon-break alert delivery (§7.9) are
  deferred follow-ups — not wired by this endpoint.
- `POST /api/oauth/saxo/start`, `GET /api/oauth/saxo/callback`,
  `GET /api/oauth/saxo/status`, `DELETE /api/oauth/saxo`.
- `POST /api/assistant` — the **grounded screen-aware assistant** (P6 / MAT-LEGIBILITY-assistant).
  Body: `{question, underlying?, trade_date?, mode?, element_id?, gloss?}`. The BFF builds a typed
  **grounding context** from the *same* store reads `/api/analytics` and the coverage notion serve
  (`projected_option_analytics` for the reference-tenor smile → ATM / 25Δ skew / convexity, mirroring
  the front's `computeScorecards`; `market_state_snapshots` for the coverage count via the canonical
  `is_valid_two_sided`), formats every number through a **server-side mirror of `sci`/`sciUnit`/`UNITS`**
  (`sci_format.py`, byte-identical to `web/src/lib/format.ts`), resolves the **close instant via the
  shared `grounding.resolve_close_instant`** (registry calendar + `option_settlement_close` → the
  venue time-of-day + zone, e.g. SX5E OESX settlement `17:30 CEST` in summer / `17:30 CET` in winter,
  never the 22:00 XEUR futures close — the same helper `/api/analytics` uses, one source), and tags
  the frame `INDICATIF`
  when `mode=indicative`. It composes a system+user prompt whose only citable numbers are that facts
  block, calls **OpenRouter** (never the browser), then **validates the answer's numbers against the
  facts block**: any number not in the block flags `grounded=false` and the answer is replaced with the
  honest-gap copy — the model **cannot** state a number the screen never showed. Returns
  `{answer, grounded, rejected_numbers, citations[], frame}`. A model/OpenRouter failure is a labelled
  non-500 (`502 assistant_unavailable` carrying the frame), never a bare 500. `gloss=true` routes a
  one-line element gloss to the cheaper model; the default reasoning route is `claude-opus-4-8`.
  `POST /api/assistant/stream` relays the model's tokens as `text/plain` for a typing UI.

The OAuth flow's verifiable half (single-use CSRF state, authorize-URL construction,
replay/forgery rejection) is real; the token exchange fails closed with a typed `501`
until `packages/infra-saxo` lands.

## Assistant configuration (OpenRouter)

The assistant calls OpenRouter (OpenAI-compatible base `https://openrouter.ai/api/v1`) from the BFF.
Config is read from the environment / gitignored `.env` — the key **never** ships to the browser and
is **never** committed:

- `OPENROUTER_API_KEY` — required for live answers. Absent → the client raises before any network call
  and `/api/assistant` returns a labelled `502` (the page renders a loud unavailable banner, never a
  fabricated answer). Tests always stub OpenRouter; the key is never needed in CI.
- `ASSISTANT_MODEL` — the grounded-reasoning model slug (default `anthropic/claude-opus-4-8`).
- `ASSISTANT_GLOSS_MODEL` — the cheaper model for one-line element glosses
  (default `anthropic/claude-haiku-4-5`).
- `OPENROUTER_HTTP_REFERER` / `OPENROUTER_APP_TITLE` — optional OpenRouter attribution headers.

## The live-run build path (SAMPLE)

A surface build runs the unified collection seam (`orchestration.build_surface` over
`collect_live`, ADR 0027) end to end. The `SAMPLE` provider drives it deterministically:
`runner.py` reads the store's most recent committed day, replays it through the **exact**
actor pipeline into a **throwaway temp store** (`persist=False`, so a SAMPLE run never
writes back into `data/` — re-capturing the same content-addressed events would be a
no-op append anyway), and reduces the fitted surface to a small job summary the web app
polls. The queue/poll/state-machine job lifecycle wraps it; any failure marks the job
`ERROR` and is logged. A run needs a committed day to replay — a `SAMPLE` against an
empty store fails fast with a typed error.

As the build walks, the runner reports the step it has **reached** — not a timer — through
the additive-nullable `stage` / `stage_index` / `stage_total` fields on `JobStatus`
(carried by `/api/jobs` and `/api/jobs/{id}`). `stage` is a PM-register French label
(`Collecte de la chaîne d'options`, `Ajustement de la nappe`, …), mapped once in
`job_stages.py` from the build's ordered steps; the engine enum never reaches the wire.
The fields stay `null` until a stage is reached (and for any non-`SAMPLE` provider), so the
front falls back to an honest indeterminate bar rather than a fabricated percent. Stage
reporting can never throw into the job boundary. State is per-process (`PipelineRunner.jobs`
is an in-memory dict); progress lives only as long as the BFF process.

Live broker providers (Saxo/Deribit/IBKR) capture through the same `build_surface` seam;
the broker-session → `RawMarketEvent` normalization lives in the
`packages/infra-{saxo,deribit,ibkr}` adapters. See `runner.py` and `infra/orchestration`.

## Verify

Python API tests (run under the root gate; `pythonpath`/testpaths are wired in the root
`pyproject.toml`):

```
uv run pytest apps/frontend/tests -q
```

Web gate:

```
cd apps/frontend/web
npm run lint
npm test
npm run build
```

The repo-wide Python gate:

```
uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q
```
