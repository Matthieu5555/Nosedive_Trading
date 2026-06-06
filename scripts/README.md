# scripts/ — operator CLI tooling

Standalone command-line tools that sit *above* the workspace packages. This directory is **not** a
uv-workspace member and is **not** in the root gate — these are operator conveniences, not library
code. They import the canonical `algotrading.*` packages and are run with `uv run`.

Plotting/export tools need the `notebooks` dependency group (matplotlib/plotly/kaleido/mistune/
nbformat): run them with `uv run --group notebooks python scripts/<tool>.py`.

## Tools

| Tool | What it does | Run |
|---|---|---|
| `plot_live_surface.py` | Replays a stored raw day through the actor pipeline (`orchestration.build_surface` over `collectors.ReplaySource`) and renders the fitted 3D IV surface to a standalone HTML. Reads `data/` **read-only**; the re-derivation runs in a throwaway temp store. | `uv run --group notebooks python scripts/plot_live_surface.py --symbol AAPL --date 2026-05-29 --out /tmp/surf.html` |
| `export_sample.py` | Computes the curated last-tick slice of a stored raw day (exactly what the snapshot consumes) and reports it. **Does not write the sample yet** — see the gap note below. | `uv run python scripts/export_sample.py --symbol AAPL --out path/to/sample.json` |
| `reconstruct_sample.py` | Decodes a committed JSON sample (`packages/infra-{saxo,ibkr}/samples/`), checks it replays deterministically, and prints the chain shape — offline, no broker. | `uv run python scripts/reconstruct_sample.py --sample packages/infra-ibkr/samples/spy_real_2026-06-04.json --symbol SPY` |
| `export_notebook_figs.py` | Re-renders the vol-surface notebook figures to PNG under `documentation/vol-surface/assets`. | `uv run --group notebooks python scripts/export_notebook_figs.py` |
| `export_doc_pdf.py` | Renders a Markdown doc (default the vol-surface pedagogy) to a clean PDF via mistune + headless Chrome/Chromium. | `uv run --group notebooks python scripts/export_doc_pdf.py` |

## Provenance & re-pointing (2026-06-05)

Ported from the pre-merge reference tree and re-pointed to current canonical APIs:
`core.config.load_platform_config` (takes the `configs/` dir), `infra.collectors.replay_day` / `ReplaySource`,
`infra.orchestration.build_surface`, `infra.storage.{ParquetStore,events_from_json,events_to_json}`.
The pre-merge `IbkrFlow`/`SaxoFlow` façades and the in-memory `pipeline.reconstruct_day(events, …)`
signature are gone; collection is unified on the push `RawCollector` seam (ADR 0027).

One known gap underlies two tools, documented in the scripts rather than worked around: the
**broker-raw ↔ contracts `RawMarketEvent` schema split**. The committed JSON samples
(`storage.events_to_json` format) use the broker-raw schema — `field_value` / `provider` and
colon-delimited `OPT:` keys (`infra.universe.parse_instrument_key`). A day read back from the store
via `collectors.replay_day` uses the contracts schema — `value` and pipe-delimited keys. They are
different classes with different key formats, and no translation layer exists in `packages/infra`
today (the relocation deferred under ADR 0021, see
`packages/infra-{saxo,ibkr}/tests/test_real_sample_reconstruct.py`). Consequently:

- `reconstruct_sample.py` validates and summarises a sample but does **not** rebuild a surface from
  it.
- `export_sample.py` computes the curated last-tick set from a stored day but does **not** serialize
  it to a sample (the store schema can't feed `events_to_json`).

For a real surface render today, `plot_live_surface.py` works end to end off a stored raw day
(contracts-schema events the actor consumes). When the schema bridge lands in `packages/infra`, both
deferred halves can be wired through it.
