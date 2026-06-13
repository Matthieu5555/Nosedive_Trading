# Routing map — where things live

This is a routing table, not a description. It points; the detail lives in each
directory's `README.md`. It stays short and rarely goes stale because directory
locations change far less often than file contents. When you add or move a
top-level area, update this file in the same change.

| Area | Path | What it owns | Start here |
|------|------|--------------|------------|
| Monorepo | `packages/` + `apps/` | The layered uv-workspace — the whole system in one tree. Layer order `core ← infra ← infra-ibkr ← {strategy, execution} ← apps/frontend`, enforced by import-linter (`pyproject.toml`); the gate and layer order live in `AGENTS.md`. **`core`** = `algotrading.core` (config/log/manifest/provenance — level 0). **`infra`** = the frozen `contracts` seam + the market-data plane, the pure analytics core, risk, QC, orchestration, storage. **`infra-ibkr`** = the IBKR broker leaf (the sole live broker; index-only). **`{strategy, execution}`** + **`apps/frontend`** (Python BFF + React/Vite web) = the upper layers. Each package's `README.md` is the next hop; ADRs are indexed in [`decisions/README.md`](decisions/README.md). | `pyproject.toml` |
| Plan of record (domain + strategy) | `TARGET.md` | **The single source — what we build, why, in what order, and the domain authority on any formula/contract conflict** (§0 frozen scope + universe model, §7 ordered sequence). `BIG_PICTURE.md` (repo root) is the system overview; `.agent/open-questions.md` is the canonical pending-decision register. `TARGET.md` is the operative plan; the `documentation/` reference corpus (blueprint, transcripts, vol-surface) is restored and tracked (ADR 0011) — see the Reference row below. | `TARGET.md` |
| Scripts | `scripts/` | Operator CLI tooling above the workspace (surface plotting, sample export/reconstruct, figure/PDF export). **Not** a uv-workspace member and **not** in the root gate — run with `uv run` (plotting/export tools need `--group notebooks`). | `scripts/README.md` |
| Configs | `configs/` | Economic + operational config as six YAML bundles (`environment`/`broker`/`universe`/`qc`/`scenarios`/`pricing`); `load_platform_config(configs/)` builds the typed `PlatformConfig` from the four economic bundles. Per ADR 0028 the standard is YAML profiles → typed validated config → DI into pure compute; config is as-of/effective-dated and frozen per run. No business parameter as a `.py` literal. | [ADR 0028](decisions/0028-configuration-and-reproducibility-standard.md) |
| Notebooks | `notebooks/` | Demo pipelines (IBKR is live; the `demo_pipeline_saxo`/`demo_pipeline_deribit` notebooks are **stale** — those packages were removed, index-only), pricing/Greeks demo, and surface-fit demo. Wire to the merged infra; honor as-of reproducibility rules — all data access must use the as-of abstraction, no wall-clock reads inside a notebook cell. | `notebooks/README.md` |
| Research | `research/` | Research notes and experiments. Reproducibility and as-of discipline rules. | `research/README.md` |
| Data | `data/` | Shared datasets (parquet/duckdb). Large/secret data stays out of git. | `data/README.md` |
| Tasks | `tasks/` | In-flight work claims (collision guard) and archived task notes. | `tasks/TASKBOARD.md` |
| Conventions | `.agent/conventions.md` | House style for code in any area. | `.agent/conventions.md` |
| Glossary | `.agent/glossary.md` | Domain vocabulary an agent won't infer. | `.agent/glossary.md` |
| Decisions | `.agent/decisions/` | Process/architecture ADRs — **read `decisions/README.md` (one-line index) first**; open a body only for the *why*. Domain lives in `TARGET.md`, not here. Superseded/dead ADRs are removed (git history is the archive). | `.agent/decisions/README.md` |
| Reference | `documentation/` | First-party reference corpus: the blueprint (authoritative on any formula/contract question, ADR 0011), course transcripts, and the vol-surface pedagogical set. Tracked in-repo; read it before asking the owner what it already specifies. | `documentation/blueprint/README.md` |

Not part of the canonical structure: `ThomasHossen/` is personal scratch space,
not covered by these conventions.
