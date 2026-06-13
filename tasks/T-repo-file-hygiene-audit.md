# T-repo-file-hygiene-audit — parasitic-file & dead-link sweep (orthogonal hygiene lane)

> **Read-only audit + safe-only cleanup, run 2026-06-13 as a parallel lane** while the priority
> queue and `T-agent-context-minimization` (matthieu) were in flight. Scope was deliberately
> **disjoint**: no `.agent/`, no `AGENTS.md`/`TARGET.md`/`TASKBOARD.md`, no held READMEs, no front
> (vincent) or basket/risk (anthony) source. Staged by explicit path; nothing committed.
> This file is the findings record — each row carries its **owning lane** so nobody redoes it.

## Done in this lane (safe, unheld, applied)

1. **Bytecode purge — project source only.** Removed 35 `__pycache__/` dirs + 390 `.pyc` under the
   project tree (`packages/`, `apps/`, `scripts/`, `notebooks/`, `configs/`). **Excluded** `.venv/`,
   `.uv-python/`, `node_modules/`, `.git/`, and the **tool caches** (`.mypy_cache`, `.ruff_cache`,
   `.pytest_cache`, `.hypothesis` — vincent's, possibly mid-run). All gitignored → **zero git diff**.
   *(Note: a first pass flagged "144 orphan `.pyc`" — that was a false positive; pytest
   assertion-rewrite caches carry a `-pytest-9.0.3` suffix the orphan check mis-parsed. Sources exist.)*
2. **Three dead task→task links repointed** (files were git-clean, unclaimed, NOT in matthieu's
   stated ADR-link sweep — disjoint from his lane):
   - `tasks/2D-strategy-composition.md`: `](2A-basket-builder.md)` → `](archive/2A-basket-builder.md)`;
     `](2B-stress-scenario.md)` → `](archive/2B-stress-scenario.md)` (both specs moved to `archive/`).
   - `tasks/T-pricing-config-completeness.md` & `tasks/T-scenario-rate-axis.md`: the audit `[report]`
     link `AUDIT-INTENT-VS-DELIVERY-2026-06-12.md` → `T-intent-vs-delivery-audit.md` (renamed file,
     same 2026-06-12 audit; findings An-4/Rk-1/Lane-0 live there).

## Findings — NOT actioned (owned by another lane; flagged so they're not lost)

### → `T-agent-context-minimization` (matthieu) — context/doc lane

- **39 tracked files still point at the deleted `documentation/` tree** (`git grep -l 'documentation/'`,
  excl. `tasks/archive/`). Clusters: `.agent/decisions/*` ADR bodies (0004, 0011, 0023, 0027, 0028,
  0030–0033, 0042) + `map.md`; **code docstrings** in `packages/infra/.../risk/{aggregation,bumps,greeks,scenarios}.py`;
  several `scripts/*.py`; many `tasks/*.md`. These are dead pointers to a removed tree — exactly the
  Part-A/Part-B "stop pointing at removed docs" cleanup. **Gate-safe** (doc-freshness only checks
  `map.md` + READMEs), so they rot silently. `map.md` is the one that the gate *does* cover — verify it.
- **Dead ADR/doc links outside the gate's view** (sample): `AGENTS.md → decisions/0023-…` (dead);
  `.agent/decisions/0028/0030–0035 → ../../documentation/…` and `→ ../../tasks/{D1,1J,T-bridge}.md`.
  All inside matthieu's held `.agent/`.
- **`BIG_PICTURE.md`** (root): **FIXED 2026-06-13** — `ThomasOssen/…_v4.pdf` typo corrected to the real
  `ThomasHossen/industrial_vol_roadmap.pdf`; the dead `tasks/AUDIT-library-leverage-2026-06-07.md` link
  de-linked (that audit + its REP0–REP8 backlog are retired to git history). No dead links remain.
  (Retirement-vs-keep of the whole file is still an owner call — see the docs survey below.)

### → broker-scope code lane (matthieu Part B item 1 — already documented there)

- `provider: str = "DERIBIT"` default in `storage/events.py:53` and `collectors/normalize.py:60`
  (the provider *dimension* is correctly generic per ADR 0017; only the literal default should be
  `"IBKR"`). Already captured in `T-agent-context-minimization` Part B — **not duplicated here.**

### → gate-test maintenance — DONE 2026-06-13

- `packages/infra/tests/test_doc_freshness.py` `REFERENCE_DIRS` **trimmed to `{"ThomasHossen"}`** — the
  stale `"Test Lenny"` / `"Vincent's Code"` entries (dirs long gone) removed. Harmless no-ops before
  (they only *exclude* dirs from the routing requirement), now just honest. Gate re-run green.

## Not parasitic (verified clean — recorded so it isn't re-investigated)

- **Tracked tree carries no junk:** 0 committed `.pyc/.pyo/.bak/.swp/.orig/.tmp/.log/~`/`.DS_Store`.
- **Empty tracked files are all legitimate:** PEP 561 `py.typed` markers + package `__init__.py`.
- **All disk cruft is gitignored** (`.coverage`, `.env`, `.venv/`, `data/*`, the tool caches, etc.).
- **`ThomasHossen/`** is an intentional reference checkout (flagged in the doc-freshness
  `REFERENCE_DIRS`), not stray — owned by matthieu, gitignored. Not for this lane to remove.

## Docs survey — useless / stale (requested 2026-06-13)

179 tracked `*.md`, 16,696 lines. The stale mass is concentrated, not scattered:

- **`.agent/decisions/0022-m5-vendored-broker-slice.md`** — a **reversed** decision (vendored broker
  slice abandoned). Already slated for untracking in `T-agent-context-minimization` (DEFERRED until
  matthieu commits his held copy). → **his lane.**
- **39-file dead `documentation/` pointer cluster** (see Findings above) — the single biggest stale-doc
  signal. → **context-min lane.**
- **`BIG_PICTURE.md`** (root, 145 L) — dead link + `ThomasOssen`→`ThomasHossen` path typo, and
  **candidate for retirement**: it bills itself as the "how-we-build companion" to `TARGET.md`, but
  `TARGET.md` has since absorbed the blueprint + transcript. Owner call whether it still earns its
  place or folds into `TARGET.md`. **Not touched.**
- **`apps/frontend/README.md`** — 5 residual saxo/deribit scope refs. → front / e2e lane.
- **`tasks/archive/*`** — heavy saxo/deribit mentions (e.g. `T-index-only-refactor.md`, 28) are
  **legitimate history** — the archive *records* the removal. **Explicitly leave; do not "clean".**
- **`.claude/skills/*`** — tool skills, not project docs. Out of scope.

**Net for an owner to action:** retire/untrack `0022`; sweep the 39 `documentation/` pointers; rule on
`BIG_PICTURE.md`. Everything else is either another lane's or intentional history.

## Disposition

The only state changed on disk: gitignored bytecode (git-invisible) + 3 task-file link repoints
(`tasks/2D-strategy-composition.md`, `tasks/T-pricing-config-completeness.md`,
`tasks/T-scenario-rate-axis.md`). Stage those three by explicit path if committing. Everything else
is a **flag for its owning lane**, not an edit.
