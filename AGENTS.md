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

Choices that are *not yet ruled on* live in `.agent/open-questions.md` — a living
register of decisions awaiting an owner/domain ruling. When you hit a fork that is
not yours to settle, record it there rather than guessing; when it is ruled it
becomes an ADR. The merge/convergence is closed. The **plan of record** — the end-state
capability map *and* the ordered build sequence — is `TARGET.md` (repo root); it is the single
roadmap. The live task board (`tasks/TASKBOARD.md`) tracks who is touching what and the ready
queue of open specs.

## Verify before you declare done

**The monorepo (`packages/**`, `apps/**`) — the full gate, run from the repo root:**
```
uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q
```
This is the **only** gate. `ruff`, `mypy`, `import-linter`, and `pytest` (with
`hypothesis`) are dev dependencies in the root `pyproject.toml`. `lint-imports` enforces
the layering (`core ← infra ← {infra-<broker>} ← {strategy,execution} ← frontend`, and
"infra is blind to alpha"): treat a broken contract as a build failure, not a warning.
Branch coverage on the analytics/risk core is a separate, deliberate step:
`uv run pytest --cov`. The gate scopes to `packages/` + `apps/` + `scripts/` (ruff and mypy cover the operational scripts too); it deliberately
excludes the read-only reference checkout, notebooks, and scratch dirs.

**frontend/** (Vite/JS): the React/Vite web app under `apps/frontend/web` verifies with
`npm run lint && npm test` (ESLint + Vitest component tests); its Python BFF is covered by the
root gate. There is **also** a real-browser **Playwright** end-to-end suite — `npm run e2e` — that
covers what jsdom structurally cannot: navigation/button flows and layout-collision / overflow
checks. It is deliberately **opt-in** (needs a browser binary + a dev server), not in the gate —
but it exists, so don't reinvent it: when you touch a page, a route, or shared layout, run it and
keep it green, and extend it for new UI. How to run and write it lives in `apps/frontend/README.md`.

If a gate cannot run because the tooling is absent, say so plainly. Do not claim
verification you did not perform.

Report status in the verb that matches what actually happened. "Done" / "verified"
/ "working" mean the thing **ran to completion and you saw the result**. Setup,
staging, wiring, or a synced worktree is "staged," not "done" — say which.
Estimates are estimates: label a number you modelled as modelled, give the load-bearing
assumption, and never dress it as a measurement. When the report would otherwise read
as "everything's handled," it must be true end to end, not true of the prep.

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
- **Lean on well-maintained libraries; do not hand-roll what one already does.**
  When a proven, maintained library covers the job, use it — pydantic for
  validation/coercion/typed config, Nautilus for the finance runtime spine
  (catalog, replay/backtest, actor host, broker lifecycle), QuantLib/`py_vollib`
  for pricing/Greeks/IV, SciPy/NumPy for numerics, DuckDB/PyArrow/Polars for
  analytical storage, pycryptodome for crypto, `secrets` for CSPRNG. Re-implementing
  a library's job in-house is a defect to be removed, not a style choice. The one
  sanctioned exception is the bespoke vol math no library provides. The bar for a
  wrapper is depth: it must hide real complexity, never be a thin shim that only
  adds a dependency. See `.agent/conventions.md` and
  [ADR 0023](decisions/0023-nautilus-runtime-spine-and-library-leverage.md).
- **Prove the environment before you believe it — measure, don't read.** A label
  in a task file, a docstring, an ADR, a prior agent's summary, or your own earlier
  claim is a *hypothesis*, not a fact. Before asserting that something is blocked,
  done, present, broken, or absent: run it. Execute the test, list the file, hit the
  port, check the credential, run the gate. State conclusions as "measured: …" with
  the command that proved it — never "blocked because the spec says so." Task specs
  drift (a spec called a built, green-tested module "absent" the day after it
  landed); the running system does not lie. See the `probe-environment` skill.
  Probe through the **real code path**, not a proxy: a health page, a redirect, a
  banner, or an HTTP status off a hand-rolled `curl` is not the thing you care about.
  (A bare `curl https://localhost:5000/` 302-ing to a login page does **not** mean the
  IBKR gateway is unauthenticated — the real check is `CpRestSession.authenticated()`;
  see `packages/infra-ibkr/README.md`.) Match how production asks the question before
  you call something down.
