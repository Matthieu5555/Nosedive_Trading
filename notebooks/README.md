# notebooks

Visualization and demonstration surfaces for the analytics/risk spine. Two formats live here:
the **marimo apps** under [`apps/`](apps/) (the maintained showcase — they launch as interactive
apps), and a small set of **Jupyter `.ipynb`** demos (kept for reference; the marimo apps are the
continuously-verified path). **Discipline (both formats):** a notebook only *imports and calls*
the tested engines and plots the result. No pricing, calibration, or analytics logic ever lives
here — production logic stays in the tested library (a formula must never be written in a notebook
and copied into the code).

## Marimo apps (`apps/`) — run-mode, maintained

Interactive [marimo](https://marimo.io) apps that drive the real engines. In `marimo run` mode the
code is hidden and only the controls and outputs show, so each one behaves like a small app. They
share one helper module, [`apps/_shared.py`](apps/_shared.py) (Plotly theme + the offline
sample-replay path — not a notebook itself). Verified to execute end-to-end against the current API
(`marimo export html` runs every cell green).

| App | Shows | Data |
|---|---|---|
| `apps/pricing_greeks.py` | Pricing & Greeks explorer: price + 6 Greeks vs spot/vol, European (analytic) vs American (lattice), live from sliders. | synthetic, no credentials |
| `apps/dollar_greeks_grid.py` | Dollar-Greeks grid: the four dollar-Greeks (Δ\$/Γ\$/V\$/Θ\$) off a real snapshot, projected onto the pinned tenor × delta-band grid — band-profile + term-structure views. Mirrors the front's `projected_analytics`; supplies the provider that gates the grid, honest about fitted-span coverage. | committed IBKR/Saxo samples (offline replay) |
| `apps/vol_surface_studio.py` | Flagship. Tab 1: interactive SVI slice calibration. Tab 2: full offline replay of a committed broker sample through `reconstruct_day` → spread, forward basis, per-expiry smile, fitted-SVI overlay, 3D IV surface, summary table. | synthetic + committed IBKR/Saxo samples |
| `apps/risk_scenario.py` | Risk & scenario explorer: a small option book priced with `position_risk`, net-Greek KPIs, `scenario_grid`/`build_scenario_report` PnL, worst-case, Taylor attribution. | synthetic, no credentials |
| `apps/qc_observability.py` | QC & observability panel: anomaly detection (`validation`), QC checks on a synthetic slice, the five orchestration alerts, dashboard render, Prometheus metrics readout. | synthetic, no credentials |
| `apps/vol_surface_pedagogique.py` | Pedagogy: vol-surface intuition, smile anatomy, the five Greeks, term structure, 3D surfaces, calendar-arb — with sliders. numpy/scipy/plotly only (no engine calls). Companion to `documentation/vol-surface/`. | synthetic, no credentials |

```bash
uv run --group notebooks marimo run  notebooks/apps/vol_surface_studio.py   # launch as an app (read-only)
uv run --group notebooks marimo edit notebooks/apps/vol_surface_studio.py    # open the reactive editor
# headless end-to-end check (executes every cell):
uv run --group notebooks marimo export html notebooks/apps/pricing_greeks.py -o /tmp/x.html
```

## Demos (Jupyter `.ipynb`)
| Notebook | Shows |
|---|---|
| `demo_pipeline_ibkr.ipynb` | Full IBKR pipeline end to end on a **committed** real sample (ASML options, EUREX) — 100% offline replay through `orchestration.reconstruction.reconstruct_day` (snapshots → QC → forward → IV inversion → SVI surface), no Gateway/token. A `RUN_LIVE=False` credential-gated cell uses the unified collector for a real feed. |
| `vol_surface_mock.ipynb` | Vol-surface render mock on **real captured** SX5E analytics — validates the front's 3D nappe before touching `charts.tsx`: log-moneyness axis, dense SVI-sampled surface, and palette/colour-range comparisons. |
| `vol_surface_pedagogique.ipynb` | Interactive pedagogy: vol surface intuition, smile anatomy, Greeks, no-arb diagnostics. Companion to `documentation/vol-surface/vol_surface_pedagogique.md`. |

> The `.ipynb` demos import and call the high-level orchestration use-cases (`reconstruct_day` /
> `build_surface`), never re-stitched low-level engines. Per ADR 0023 Nautilus is the runtime
> spine; per ADR 0027 collection is unified on one `RawCollector`. The marimo apps above are the
> maintained, continuously-verified path — treat the notebooks as reference demos that may need an
> occasional API refresh.
>
> Known gap (minimalism sweep): the broker samples are in the broker-raw `RawMarketEvent` schema
> while the store/actor use the contracts schema; the IBKR notebook bridges it with a tiny
> in-notebook relabelling helper until the two shapes are collapsed (deferred ADR 0021).

## Run
```bash
uv run --group notebooks jupyter lab        # open and run interactively
# or execute headless:
uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/vol_surface_mock.ipynb
```
Plots render inline. The notebooks are kept output-free in git (run them to regenerate the plots).

> Maintained as plain Jupyter `.ipynb`; `jupytext` is available (in the `notebooks` dependency
> group) to round-trip a notebook to a percent-format `.py` for clean diffs when needed.
