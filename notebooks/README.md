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
| `demo_pipeline_ibkr.ipynb` | IBKR pipeline demo: reconstruct a surface from a committed real sample through the actor pipeline (no Gateway needed); a credential-gated live cell uses the unified collector. |
| `demo_pipeline_saxo.ipynb` | Saxo pipeline demo: reconstruct a surface from a committed real sample (no token needed); a credential-gated live cell uses the unified collector. |
| `vol_surface_pedagogique.ipynb` | Interactive pedagogy: vol surface intuition, smile anatomy, Greeks, no-arb diagnostics. Companion to `documentation/vol-surface/vol_surface_pedagogique.md`. |

> **Status (post-merge): all seven demos are rewired to the merged API and verified.**
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
