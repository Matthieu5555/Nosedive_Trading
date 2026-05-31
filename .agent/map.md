# Routing map — where things live

This is a routing table, not a description. It points; the detail lives in each
directory's `README.md`. It stays short and rarely goes stale because directory
locations change far less often than file contents. When you add or move a
top-level area, update this file in the same change.

| Area | Path | What it owns | Start here |
|------|------|--------------|------------|
| Backend | `backend/` | Python service and quant logic (FastAPI, numpy/pandas/polars). Currently a skeleton. | `backend/README.md` |
| Frontend | `frontend/` | JS/Vite app. Not scaffolded yet. | `frontend/README.md` |
| Research | `research/` | Notebooks and experiments. Reproducibility and as-of discipline rules. | `research/README.md` |
| Data | `data/` | Shared datasets (parquet/duckdb). Large/secret data stays out of git. | `data/README.md` |
| Tasks | `tasks/` | In-flight work claims (collision guard) and archived task notes. | `tasks/TASKBOARD.md` |
| Conventions | `.agent/conventions.md` | House style for code in any area. | `.agent/conventions.md` |
| Glossary | `.agent/glossary.md` | Domain vocabulary an agent won't infer. | `.agent/glossary.md` |
| Decisions | `.agent/decisions/` | Append-only ADRs for non-obvious choices. | `.agent/decisions/` |

Not part of the canonical structure: `ThomasOssen/` is personal scratch space,
not covered by these conventions.
