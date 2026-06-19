# Routing map — where things live

This is a routing table, not a description. It points; the detail lives in each
directory's `README.md`. It stays short and rarely goes stale because directory
locations change far less often than file contents. When you add or move a
top-level area, update this file in the same change.

| Area | Path | What it owns | Start here |
|------|------|--------------|------------|
| Monorepo | `packages/` + `apps/` | The layered uv-workspace — the whole system in one tree. Layer order `core ← infra ← infra-ibkr ← {strategy, execution} ← apps/frontend`, enforced by import-linter (`pyproject.toml`); the gate and layer order live in `AGENTS.md`. **`core`** = `algotrading.core` (config/log/manifest/provenance — level 0). **`infra`** = the frozen `contracts` seam + the market-data plane, the pure analytics core, risk, QC, orchestration, storage. **`infra-ibkr`** = the IBKR broker leaf (the sole live broker; index-only). **`{strategy, execution}`** + **`apps/frontend`** (Python BFF + React/Vite web) = the upper layers. Each package's `README.md` is the next hop. | `pyproject.toml` |
| Plan of record (domain + strategy) | `TARGET.md` | **The single source — what we build, why, in what order, and the domain authority on any formula/contract conflict** (§0 frozen scope + universe model, §7 ordered sequence). `BIG_PICTURE.md` (repo root) is the system overview. `TARGET.md` absorbed the retired blueprint — it is the sole domain authority. There is no decision ledger and no pending-decision register; current state lives here and in the READMEs, the *why* is in git history, and unresolved forks go to the owner. The `documentation/` tree is gone; git history is the archive. For course pedagogy (formulas, strategy rationale), see `ThomasHossen/MM_options_trading.md`. | `TARGET.md` |
| Scripts | `scripts/` | Operator CLI tooling above the workspace (surface plotting, sample export/reconstruct, figure/PDF export). **Not** a uv-workspace member and **not** in the root gate — run with `uv run` (plotting/export tools need `--group notebooks`). | `scripts/README.md` |
| Configs | `configs/` | Economic + operational config as six YAML bundles (`environment`/`broker`/`universe`/`qc`/`scenarios`/`pricing`); `load_platform_config(configs/)` builds the typed `PlatformConfig` from the four economic bundles. Per ADR 0028 the standard is YAML profiles → typed validated config → DI into pure compute; config is as-of/effective-dated and frozen per run. No business parameter as a `.py` literal. | `configs/README.md` |
| Notebooks | `notebooks/` | Demo pipelines (IBKR/SX5E, index-only), pricing/Greeks demo, and surface-fit demo. Wire to the merged infra; honor as-of reproducibility rules — all data access must use the as-of abstraction, no wall-clock reads inside a notebook cell. | `notebooks/README.md` |
| Research | `research/` | Research notes and experiments. Reproducibility and as-of discipline rules. | `research/README.md` |
| Data | `data/` | Shared datasets (parquet/duckdb). Large/secret data stays out of git. | `data/README.md` |
| Tasks | `tasks/` | In-flight work claims (collision guard) and archived task notes. | `tasks/TASKBOARD.md` |
| Docs | `docs/` | Relocated reference documentation: the `blueprint/` tree (the as-transcribed plan-of-record source, ADR 0011), plus connectivity guides and supporting notes. Routing only — the canonical-vs-absorbed status of individual docs is governed by ADR 0011 / `platform-doc-coherence-fix`. | `docs/blueprint/README.md` |
| Conventions | `.agent/conventions.md` | House style for code in any area. | `.agent/conventions.md` |
| Glossary | `.agent/glossary.md` | Domain vocabulary an agent won't infer. | `.agent/glossary.md` |
| Voice | `.agent/voice.md` | How agents talk to people here — plain prose, no jargon, no markdown for humans. | `.agent/voice.md` |
| Course pedagogy | `ThomasHossen/MM_options_trading.md` | The prof's course notes: strategy rationale, Greek intuition, formulas (RT-Vega, mirror Greeks, smile pedagogy, vol-surface blocs). This is the canonical live copy; `documentation/` was removed (ADR 0011 amended; git history is the archive). | `ThomasHossen/MM_options_trading.md` |

Not part of the canonical structure: `ThomasHossen/` is personal scratch space — but `ThomasHossen/MM_options_trading.md` is the live course-pedagogy reference (see above).
