# Routing map — where things live

This is a routing table, not a description. It points; the detail lives in each
directory's `README.md`. It stays short and rarely goes stale because directory
locations change far less often than file contents. When you add or move a
top-level area, update this file in the same change.

| Area | Path | What it owns | Start here |
|------|------|--------------|------------|
| Backend | `backend/` | Python service and quant logic (FastAPI, numpy/pandas/polars). Foundation (A): typed contracts, config/provenance, DuckDB-over-Parquet storage, fixtures, quality gate. Market-data plane (B): broker-agnostic connectivity, the instrument universe, and the append-only collector in `src/{connectivity,universe,collectors}` (each with a README). Analytics core (C): the pure-function quant heart — pricing engine, snapshot builder, forward/carry, IV solver, volatility surface in `src/{pricing,snapshots,forwards,iv,surfaces}` (each with a README). Risk engine (D): portfolio Greeks, monetized sensitivities, aggregation, broker reconciliation, and the scenario stress engine in `src/risk` (README + ADR 0006), built on C's frozen pricer. Integration & operations (E): the framework-free Nautilus actor that drives C/D and stamps their outputs in `src/actor`, the QC/validation library in `src/qc`, and orchestration/observability + historical replay in `src/orchestration` (with the `reconstruction` subpackage) — README + ADR 0007. The same actor runs live and replay, which is what makes the headline byte-identical-replay and provenance-verification tests pass. | `backend/README.md` |
| Operations | `documentation/` | Operator handover (E): the five runbooks (`documentation/runbooks/`), release-management rules + release notes (`documentation/releases/`), the frozen interface-contract list, and known limitations / support model. `documentation/modules/` mirrors every per-directory `README.md` via symlink, so each module's doc lives next to its code *and* under one roof here. Start at the index. | `documentation/README.md` |
| Frontend | `frontend/` | JS/Vite app. Not scaffolded yet. | `frontend/README.md` |
| Research | `research/` | Notebooks and experiments. Reproducibility and as-of discipline rules. | `research/README.md` |
| Data | `data/` | Shared datasets (parquet/duckdb). Large/secret data stays out of git. | `data/README.md` |
| Tasks | `tasks/` | In-flight work claims (collision guard) and archived task notes. | `tasks/TASKBOARD.md` |
| Conventions | `.agent/conventions.md` | House style for code in any area. | `.agent/conventions.md` |
| Glossary | `.agent/glossary.md` | Domain vocabulary an agent won't infer. | `.agent/glossary.md` |
| Decisions | `.agent/decisions/` | Append-only ADRs for non-obvious choices. | `.agent/decisions/` |

Not part of the canonical structure: `ThomasOssen/` is personal scratch space,
not covered by these conventions.
