# Shared workspace (/srv/project)

Everyone in the `devs` group can read/write here.

**Agents and humans: read [`AGENTS.md`](AGENTS.md) first.** It is the canonical
guide for working in this workspace. To find where something lives, start at
[`.agent/map.md`](.agent/map.md), then read the relevant directory's `README.md`.

## Layout

- `backend/`   Python service & quant logic (uv, Python 3.13). The strategy-agnostic
  market-data → analytics → risk backbone (workstreams A–E) is built; there is no
  FastAPI app yet (`main.py` is still a stub). See `backend/README.md`.
- `frontend/`  JS/Vite app. Not scaffolded yet. See `frontend/README.md`.
- `research/`  Notebooks and experiments, with reproducibility and as-of rules.
- `data/`      Shared datasets (parquet/duckdb). Keep large/secret data out of git.
- `.agent/`    Agent instruction layer: routing map, conventions, glossary, decisions.
- `tasks/`     `TASKBOARD.md` — claim your work before you start (collision guard).

## Git and secrets

The workspace root is a git repository (the umbrella that version-controls the
shared instruction layer). Use branches: one per task, merge small and often.

Your AI CLIs (`claude`, `codex`) authenticate with YOUR own account — config
lives in your personal `$HOME`, not here. Don't commit secrets; put per-person
tokens in your `$HOME`, project config in a local `.env` (gitignored).
