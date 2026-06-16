# notebooks

Visualization and demonstration surfaces for the analytics/risk spine. **Discipline:** a notebook
only *imports and calls* the tested engines and plots the result. No pricing, calibration, or
analytics logic ever lives here — production logic stays in the tested library (a formula must never
be written in a notebook and copied into the code).

## `risk_dashboard.py` — the PM dashboard (start here)

One consolidated, PM-first surface that answers the four questions a portfolio manager actually
asks — *what's my risk, what explains a move, where can we blow up, where did vol go* — as
collapsible accordion sections with plain-language framing and selectors (scope / greek / name /
put-call). The seven single-purpose apps below are the component views it draws on.

There is **no positions/fills/P&L feed** in the offline store, so the dashboard *constructs* a
plausible book (a vol-seller that owns crash protection, seeded from each name's actually-available
grid cells — see `algotrading.frontend.demo_book`) to have something real to measure. The math run
on that book is the real, tested risk engine; only the positions are synthetic, and every section
says so. P&L attribution is therefore a **hypothetical scenario**, not a realised day.

| Section | PM question | Real on banked data? |
|---|---|---|
| The book | what am I holding | constructed (no live positions exist) |
| ① Scenario heatmap (spot × vol) | what if spot *and* vol move | ✅ live engine — the headline view |
| ② Greeks by expiry bucket | where does the risk sit | ✅ |
| ③ P&L attribution | which factor drives a move | ⚠️ hypothetical scenario only |
| ④ Concentration by name | which name matters | ✅ greek exposure (no P&L/stress book) |
| ⑤ Vol change (IV by expiry × band) | where did vol move | ✅ but only 2 capture dates banked |
| ⑥ Smile (≤3 expiries) | is skew steep / flat / moving | ✅ |
| ⑦ Implied vs realized vol | expensive or cheap vol | ✅ realized deep; implied = 2 dates |

```bash
uv run --group notebooks marimo run notebooks/risk_dashboard.py
```

## Interactive apps (marimo — one per frontend functionality)

The `marimo` `.py` apps are the simple, working stand-in for the web frontend: each mirrors **one**
functionality from the frontend (`apps/frontend/web`), runs in `marimo run` mode with live
selectors, and reads the **real** banked store (`data/`, offline) through the same BFF service layer
the web app uses. Banked: trade dates 2026-06-15 / 2026-06-16; underlyings SX5E + constituents
(ALV ASML ENR MC SAP SIE SU TTE SAF).

| App | Mirrors | Shows |
|---|---|---|
| `vol_surface.py` | Market → vol analytics | 3D SVI nappe + flat heatmap + ATM term structure; date/underlying selectors |
| `greeks.py` | Market → dollar greeks | Dollar greeks by maturity; date/underlying selectors |
| `market.py` | Market → price history | OHLC candles + close line for index & constituents, weighted constituents table; index/symbol/lookback selectors |
| `coverage.py` | Market → capture coverage | Captured chain strike-vs-expiry map + per-expiry coverage table + QC floor pass/fail; date/underlying selectors |
| `basket_risk.py` | Basket builder | Basket dollar greeks / leg breakdown; date/underlying + leg selectors |
| `scenarios.py` | Risk Scenarios | ±spot × ±vol stress P&L heatmap with worst-case cell; date/underlying + leg selectors |
| `attribution.py` | P&L decomposition | Greek-by-greek P&L waterfall (delta…volga → full-reprice → residual) + tolerance verdict; date/underlying + spot/vol/time shock sliders |

### Run a marimo app
```bash
uv run --group notebooks marimo run notebooks/vol_surface.py    # serve one app with live selectors
uv run --group notebooks marimo edit notebooks/vol_surface.py   # open the reactive editor
```
Each app is reactive: changing a selector re-runs only the dependent cells. Every top-level variable
name is unique across cells (marimo's dataflow requirement); cell-local throwaways are
`_`-prefixed. Verified headless with `marimo export html <app>.py` (executes every cell against the
real store).

## Demos (Jupyter `.ipynb`)
| Notebook | Shows |
|---|---|
| `demo_pipeline_ibkr.ipynb` | Full IBKR pipeline end to end on a **committed** real sample (SX5E / EuroStoxx50 index options, EUREX) — 100% offline replay through `orchestration.reconstruction.reconstruct_day` (snapshots → QC → forward → IV inversion → SVI surface), no Gateway/token. Reconstructs a rich 15-maturity surface (9d → ~3y), all slices arb-free. A `RUN_LIVE=False` credential-gated cell uses the unified collector for a real feed. |
| `vol_surface_mock.ipynb` | Vol-surface render mock on **real captured** SX5E analytics — validates the front's 3D nappe before touching `charts.tsx`: log-moneyness axis, dense SVI-sampled surface, and palette/colour-range comparisons. |
| `vol_surface_pedagogique.ipynb` | Interactive pedagogy: vol surface intuition, smile anatomy, Greeks, no-arb diagnostics. |

> The `.ipynb` demos import and call the high-level orchestration use-cases (`reconstruct_day` /
> `build_surface`), never re-stitched low-level engines. Per ADR 0023 Nautilus is the runtime
> spine; per ADR 0027 collection is unified on one `RawCollector`. Treat the notebooks as reference
> demos that may need an occasional API refresh.
>
> Known gap (minimalism sweep): the broker samples are in the broker-raw `RawMarketEvent` schema
> while the store/actor use the contracts schema; the IBKR notebook bridges it with a tiny
> in-notebook relabelling helper (ADR 0039 `events_to_contracts`) until the two shapes are collapsed.

## Run
```bash
uv run --group notebooks jupyter lab        # open and run interactively
# or execute headless:
uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/demo_pipeline_ibkr.ipynb
```
Plots render inline. The notebooks are kept output-free in git (run them to regenerate the plots).

> Maintained as plain Jupyter `.ipynb`; `jupytext` is available (in the `notebooks` dependency
> group) to round-trip a notebook to a percent-format `.py` for clean diffs when needed.
