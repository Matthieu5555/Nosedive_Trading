# notebooks

Jupyter notebooks for visualization and demonstration. **Discipline:** a notebook only *imports
and calls* the tested engines and plots the result. No pricing, calibration, or analytics logic
ever lives here — production logic stays in the tested library (a formula must never be written
in a notebook and copied into the code).

## Demos
| Notebook | Shows |
|---|---|
| `demo_surface_fit.ipynb` | SVI slice calibration: raw market points vs fitted slice (step 9 acceptance viz). |
| `demo_pricing_greeks.ipynb` | Pricing engine: price and Greeks vs spot, European (analytic) vs American (lattice). |
| `demo_pipeline_deribit.ipynb` | End-to-end pipeline demo: Deribit → collection → surface → risk. Runs against the Deribit testnet (public API, no auth). |
| `demo_pipeline_deribit_v2.ipynb` | Deribit pipeline v2: updated to post-provider-dimension and ProviderFlow seam. |
| `demo_pipeline_ibkr.ipynb` | IBKR pipeline demo: universe expansion, IbkrBrokerSession, surface collection. Requires `uv sync --extra ibkr` and a running Gateway. |
| `demo_pipeline_saxo.ipynb` | Saxo pipeline demo: OAuth2 flow, OptionsChain endpoint, IV surface. Requires a live Saxo token. |
| `vol_surface_pedagogique.ipynb` | Interactive pedagogy: vol surface intuition, smile anatomy, Greeks, no-arb diagnostics. Companion to `documentation/vol-surface/vol_surface_pedagogique.md`. |

The first two run on synthetic inputs (reproducible, no broker needed). The pipeline demos require
live credentials or a testnet; see the cell-level setup instructions in each notebook.

## Run
```bash
uv run --group notebooks jupyter lab        # open and run interactively
# or execute headless:
uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/demo_surface_fit.ipynb
```
Plots render inline. The notebooks are kept output-free in git (run them to regenerate the plots).

> Maintained as plain Jupyter `.ipynb`; `jupytext` is available (in the `notebooks` dependency
> group) to round-trip a notebook to a percent-format `.py` for clean diffs when needed.
