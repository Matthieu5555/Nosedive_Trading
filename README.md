# Shared workspace (/srv/project)

Everyone in the `devs` group can read/write here.

**Agents and humans: read [`AGENTS.md`](AGENTS.md) first.** It is the canonical
guide for working in this workspace. To find where something lives, start at
[`.agent/map.md`](.agent/map.md), then read the relevant directory's `README.md`.

## Layout

This repo is mid-merge: the original flat `backend/` build and Vincent's layered
uv-workspace monorepo are being unified under `packages/` (M0 keystone). Until M0 lands,
`backend/` remains the live codebase.

- `backend/`              Python service & quant logic (Python 3.13, uv). Market-data →
  analytics → risk backbone plus QC/validation, actor, orchestration, replay, and a FastAPI
  BFF with React frontend (M8). Gate: `cd backend && uv run ruff check . && uv run mypy . && uv run pytest -q`.
- `packages/`             Target monorepo layout (`core`, `infra`, `infra-ibkr`,
  `infra-saxo`, `infra-deribit`, `strategy`, `execution`) — scaffolded by M0, populated
  by M1–M7. See individual `packages/<pkg>/README.md`.
- `apps/frontend/`        React/Vite web app (M8). Cross-package; usable by all layers.
- `documentation/`        Operator handover runbooks, interface contracts, release notes.
- `documentation/blueprint/` Founding domain reference — formulas, data contracts,
  field definitions, 16-step roadmap. **Read this before touching any analytics code.** See
  `documentation/blueprint/README.md` and ADR 0011.
- `documentation/vol-surface/` Pedagogical vol surface doc + figures. Prerequisite before
  touching `infra/{iv,surfaces}`.
- `notebooks/`            Demo pipelines (Deribit, IBKR, Saxo), pricing/Greeks, surface fit.
- `research/`             Research notes and experiments. As-of reproducibility rules apply.
- `data/`                 Shared datasets (parquet/duckdb). Keep large/secret data out of git.
- `.agent/`               Agent instruction layer: routing map, conventions, glossary,
  decisions (ADRs 0001–0017).
- `tasks/`                `TASKBOARD.md` — claim your work before you start (collision guard).

## Git and secrets

The workspace root is a git repository (the umbrella that version-controls the
shared instruction layer). Use branches: one per task, merge small and often.

Your AI CLIs (`claude`, `codex`) authenticate with YOUR own account — config
lives in your personal `$HOME`, not here. Don't commit secrets; put per-person
tokens in your `$HOME`, project config in a local `.env` (gitignored).
