# notebooks

Visualization and demonstration surfaces for the analytics/risk spine, as plain **Jupyter
`.ipynb`** demos. **Discipline:** a notebook only *imports and calls* the tested engines and plots
the result. No pricing, calibration, or analytics logic ever lives here — production logic stays in
the tested library (a formula must never be written in a notebook and copied into the code).

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
