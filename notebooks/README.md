# notebooks

Visualization and demonstration surfaces for the analytics/risk spine. Two formats live here:
the **marimo apps** under [`apps/`](apps/) (the maintained showcase — they launch as interactive
apps), and the older **Jupyter `.ipynb`** demos (kept, but several have drifted from the current
API — see the caveat below). **Discipline (both formats):** a notebook only *imports and calls*
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
| `demo_surface_fit.ipynb` | SVI slice calibration: raw market points vs fitted slice (step 9 acceptance viz). |
| `demo_pricing_greeks.ipynb` | Pricing engine: price and Greeks vs spot, European (analytic) vs American (lattice). |
| `demo_pipeline_deribit.ipynb` | End-to-end pipeline demo: Deribit → collection → surface → risk. Runs against the Deribit testnet (public API, no auth). |
| `demo_pipeline_deribit_v2.ipynb` | Deribit pipeline v2: updated to post-provider-dimension and ProviderFlow seam. |
| `demo_pipeline_ibkr.ipynb` | IBKR pipeline demo: reconstruct a surface from a committed real sample through the actor pipeline (no Gateway needed); a credential-gated live cell uses the unified collector. |
| `demo_pipeline_saxo.ipynb` | Saxo pipeline demo: reconstruct a surface from a committed real sample (no token needed); a credential-gated live cell uses the unified collector. |
| `vol_surface_pedagogique.ipynb` | Interactive pedagogy: vol surface intuition, smile anatomy, Greeks, no-arb diagnostics. Companion to `documentation/vol-surface/vol_surface_pedagogique.md`. |

> **Caveat (measured 2026-06-09): the `.ipynb` set has drifted from the current API** — e.g.
> `demo_surface_fit` calls `fit_svi` without its now-required `config`, and the IBKR/Saxo demos
> pass a single `config_hash=` where `reconstruct_day` now takes a `config_hashes` mapping. The
> marimo apps above are the maintained, currently-verified path; the notes below describe the
> `.ipynb` set as originally written.
>
> - **No-credential, synthetic** (`demo_surface_fit`, `demo_pricing_greeks`, `vol_surface_pedagogique`) — run on reproducible synthetic inputs.
> - **Deribit** (`demo_pipeline_deribit`, `_v2`) — run end-to-end against the public Deribit **testnet** (no auth); v2 also demonstrates `orchestration.run_provider_flow`.
> - **IBKR / Saxo** (`demo_pipeline_ibkr`, `_saxo`) — run **off the committed real samples** (`packages/infra-{ibkr,saxo}/samples/*.json`) through `orchestration.reconstruction.reconstruct_day`, **no Gateway/token needed**. Each keeps a `RUN_LIVE=False` credential-gated cell that uses the unified collection seam (`orchestration.run_provider_flow`) for a real feed.
>
> All call the high-level orchestration use-cases (`build_surface` / `reconstruct_day` / `run_provider_flow`), never re-stitched low-level engines. Per ADR 0023 Nautilus is the runtime spine; per ADR 0027 collection is unified on one `RawCollector` (the old `infra_{ibkr,saxo}.flow` paths are gone).
>
> Known gap (minimalism sweep): the broker samples are in the broker-raw `RawMarketEvent` schema while the store/actor use the contracts schema; the IBKR/Saxo notebooks bridge it with a tiny in-notebook relabelling helper until the two shapes are collapsed (deferred ADR 0021).

## Run
```bash
uv run --group notebooks jupyter lab        # open and run interactively
# or execute headless:
uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/demo_surface_fit.ipynb
```
Plots render inline. The notebooks are kept output-free in git (run them to regenerate the plots).

> Maintained as plain Jupyter `.ipynb`; `jupytext` is available (in the `notebooks` dependency
> group) to round-trip a notebook to a percent-format `.py` for clean diffs when needed.
