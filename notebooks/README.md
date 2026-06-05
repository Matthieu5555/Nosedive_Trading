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

The three no-credential demos (`demo_surface_fit`, `demo_pricing_greeks`, `vol_surface_pedagogique`)
run on synthetic inputs (reproducible, no broker needed). The pipeline demos require live
credentials or a testnet; see the cell-level setup instructions in each notebook.

> **Status (2026-06-05, post-merge):** the three no-credential demos are **rewired to the merged
> API and verified** (they execute clean against `algotrading.infra.*` under `--group notebooks`).
> The four broker-pipeline demos (`demo_pipeline_{deribit,deribit_v2,ibkr,saxo}`) were carried over
> from the pre-merge tree and **still import pre-merge module paths** (e.g. `surfaces.engine`,
> `infra_{ibkr,saxo}.flow`, the old `forwards`/`iv`/`qc`/`risk`/`snapshots` names); they need a
> rewire to the merged collection seam (C6: `orchestration.provider_flow`, the unified collector)
> before they run, plus live credentials/testnet/Gateway to execute end-to-end.

> **ADR 0023:** Nautilus is the runtime spine — IBKR moved onto Nautilus's adapter (the old
> `demo_pipeline_ibkr` `IbkrBrokerSession` path is superseded), while Saxo/Deribit keep their own
> adapters. The pipeline demos will reflect that wiring once rewired.

## Run
```bash
uv run --group notebooks jupyter lab        # open and run interactively
# or execute headless:
uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/demo_surface_fit.ipynb
```
Plots render inline. The notebooks are kept output-free in git (run them to regenerate the plots).

> Maintained as plain Jupyter `.ipynb`; `jupytext` is available (in the `notebooks` dependency
> group) to round-trip a notebook to a percent-format `.py` for clean diffs when needed.
