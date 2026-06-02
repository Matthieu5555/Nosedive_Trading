# 0001 — Workspace layout and the agent-instruction layer

- **Status:** accepted
- **Date:** 2026-05-31

## Context

`/srv/project` is a shared `devs`-group workspace with several humans and AI
CLIs (`claude`, `codex`) working in it, mixing interactive and autonomous runs.
The recurring failure was that agents couldn't find where things live, and a
secondary risk was concurrent actors silently overwriting each other.

The repo was not greenfield: it already had `backend/` (Python skeleton with its
own empty git repo), an empty `frontend/`, an empty `data/`, a root `README.md`,
and a `ThomasOssen/` scratch folder. The root was not itself a git repo.

## Decision

Add a discoverability layer rooted at `/srv/project`:

- `AGENTS.md` is the single canonical instruction file. `CLAUDE.md` (and any
  future tool file) only redirects to it.
- `.agent/map.md` is a thin routing table that points at directories; it
  describes nothing itself.
- The real per-area detail lives in each directory's `README.md`, next to the
  code, so it resists staleness.
- `.agent/conventions.md` holds house style and points at the skills in
  `~/.claude/skills/` rather than restating them.
- `.agent/glossary.md` and this `decisions/` log hold knowledge agents can't infer.
- `tasks/TASKBOARD.md` is the in-repo collision signal; branch discipline is the
  real safety.

`/srv/project` was made a git repository (the umbrella) so the instruction layer
is version-controlled. `backend/`'s pre-existing git repo was empty (zero commits,
no remotes), so absorbing it into the umbrella cost no history.

## Alternatives considered

- **Docs in `backend/` only** — version-controlled with no migration, but
  doesn't cover `frontend/` or `research/`, so cross-app discoverability suffers.
  Rejected.
- **Docs at root, root left unversioned** — cheapest, but the instruction layer
  itself would have no version history. Rejected in favor of git-init.
- **A `src/<subsystem>/` single-tree** — the structure originally sketched, but
  it doesn't fit the existing multi-app (`backend`/`frontend`/`research`/`data`)
  layout. Rejected.

## Consequences

- One source of truth for conventions; tool files defer to `AGENTS.md`.
- Per-directory READMEs must be maintained as part of any change to what a
  directory does. CI enforcement of "every area has a fresh README" is a
  candidate follow-up, not yet built.
- The verify gate is documented per-app in `AGENTS.md`, not centralized, because
  backend and frontend verify differently. The backend test/lint/typecheck
  toolchain (`pytest`/`ruff`/`mypy`) is not wired yet — first quality task.

## Update 2026-05-31: skills live in-repo, not in `~/.claude/skills/`

The decision above (line 28) put the skills in `~/.claude/skills/`. That was
wrong for a shared `devs`-group, multi-agent workspace: a personal home directory
only covers one user on one machine, so every other human and agent starts with
no skills. The nine skills now live in `.claude/skills/`, committed to the
umbrella repo, so they are version-controlled and every actor gets them with no
per-machine setup. `.agent/conventions.md` was updated to match.
