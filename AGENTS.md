# AGENTS.md — canonical instructions for all agents

This file is the single source of truth for how to work in `/srv/project`.
Every agent tool defers here. `CLAUDE.md` and any future `CODEX.md` just redirect
to this file. If guidance here conflicts with anything elsewhere, this wins —
and that conflict is a bug worth fixing, because the same rule living in two
places is how multi-agent setups drift.

This file is an index and a rulebook, not a knowledge base. Detail lives next to
the code (per-directory `README.md`) and in `.agent/`. Keep this file short and
stable.

## Orient yourself in three hops

1. Read `.agent/map.md` — a routing table that says which directory owns what.
2. Read that directory's `README.md` — what it does, entry points, gotchas.
3. Read the code.

Do not search blind. The map exists so you don't have to.

## Before you touch anything

1. **Claim your work on `tasks/TASKBOARD.md`.** Write one line naming the
   files or subsystem you are about to change, who/what you are, and when.
   Clear it when done. This is a shared `devs`-group workspace with several
   humans and agents working at once; the board is the cheapest collision
   signal we have.
2. **Work on a branch, not on the shared mainline.** One branch per task, merge
   small and often. The TASKBOARD is advisory; branch discipline is the real
   safety. Collisions should surface as merge conflicts (visible, recoverable),
   never as silent overwrites (invisible, destructive).
3. **Read `.agent/conventions.md`** before writing code. It is the distilled
   house style and it points at the deeper skills.

## Conventions

See `.agent/conventions.md`. It is not restated here so it cannot drift from here.

## Voice

How to write when talking to people — chat replies, plans, PRs, summaries: plain,
direct, honest prose with minimal markdown. See `.agent/voice.md`. This applies to
every agent, every response.

## Domain vocabulary

See `.agent/glossary.md` before guessing what a domain term means. Wrong guesses
on quant/finance terms propagate into wrong code.

## Decisions

Non-obvious choices are recorded in `.agent/decisions/` as append-only ADRs.
Read the relevant one before re-litigating a design; add a new one when you make
a choice the next agent would otherwise have to reverse-engineer.

## Verify before you declare done

There is no single repo-wide verify command yet, because backend and frontend
verify differently. Run the relevant app's gate and report what actually ran.

**backend/** (Python, uv, Python 3.13) — the full gate is wired:
```
cd backend && uv run ruff check . && uv run mypy . && uv run pytest -q
```
`ruff`, `mypy`, and `pytest` (with `hypothesis`) are dev dependencies in
`backend/pyproject.toml`, and the gate runs green on the current foundation.

**frontend/** (Vite/JS): not scaffolded yet. Once it exists, the gate is
`cd frontend && npm run lint && npm test`.

If a gate cannot run because the tooling is absent, say so plainly. Do not claim
verification you did not perform.

## Keep the docs alive

When you change what a directory *does*, update that directory's `README.md` in
the same change. When you change the layout, update `.agent/map.md`. Staleness is
the thing that actually kills discoverability, so the rule is: the doc next to
the code is part of the change, not a follow-up.

## House rules that bite if ignored

- Python: `uv` for everything (`uv add`, `uv run`, `uv sync`). Never pip/poetry/conda.
- No secrets in git. Per-person tokens live in your `$HOME`; project config in a
  local gitignored `.env`.
- Tests are not optional. Code without tests is not done. Expected values are
  derived independently, never copied from the code under test.
- Financial/time-series code: no look-ahead bias. All data access through an
  as-of abstraction. See `.agent/conventions.md` and the `check-lookahead-bias` skill.
