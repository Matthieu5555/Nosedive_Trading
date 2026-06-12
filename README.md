# Shared workspace (/srv/project)

Everyone in the `devs` group can read/write here.

**Agents and humans: read [`AGENTS.md`](AGENTS.md) first.** It is the canonical
guide for working in this workspace. To find where something lives, start at
[`.agent/map.md`](.agent/map.md), then read the relevant directory's `README.md`.

## Layout

The system is one layered uv-workspace monorepo under `packages/` + `apps/` (the merge
that unified the original flat `backend/` build with Vincent's monorepo is complete; the
flat tree is retired). The single gate runs from the repo root:
`uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.

The gate and the operator entrypoints are encoded as recipes in the root
`justfile` — `just gate`, `just smoke` (offline end-to-end walk incl. the
byte-identical-replay check), `just eod [CAL]`, `just backfill`, `just login
[live|paper]`, `just web-test` — run `just --list` for the full set, or
`uv tool run --from rust-just just <recipe>` if `just` isn't installed. CI
(`.github/workflows/gate.yml`) fires the same gate, the offline smoke, and the
web app's lint+tests on every push and pull request.

- `packages/`             The single tree: `core` (`algotrading.core` — config/log/manifest/
  provenance), `infra` (`algotrading.infra` — the contract seam plus market-data, analytics,
  risk, QC/validation, the Nautilus-hosted actor, orchestration, observability, replay),
  `infra-{ibkr,saxo,deribit}` (broker leaf adapters), and `strategy`/`execution` (upper
  layers). See each `packages/<pkg>/README.md`. Layering is enforced by import-linter.
- `apps/frontend/`        Python BFF + React/Vite web app, wired to the real `infra` seams.
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
  decisions (ADRs).
- `tasks/`                `TASKBOARD.md` — claim your work before you start (collision guard).

## Git and secrets

The workspace root is a git repository (the umbrella that version-controls the
shared instruction layer). Use branches: one per task, merge small and often.

Your AI CLIs (`claude`, `codex`) authenticate with YOUR own account — config
lives in your personal `$HOME`, not here. Don't commit secrets; put per-person
tokens in your `$HOME`, project config in a local `.env` (gitignored).
