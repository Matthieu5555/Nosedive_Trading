# H1 — Repo-hygiene audit report (classification)

**Ran against:** commit `e0ab3ab` (`board: broker.yaml wiring landed; only the
effective-dated profile store remains`), on a settled tree — C7 landed, the
broker/notebooks migration committed (`00fd709`, `03fc3e8`, `91a616d`, `e0ab3ab`).
Working tree clean at audit time.

**Method:** read-only walk of every root-level path outside the canonical tree
(`packages/`, `apps/`, the `.agent/` rulebook). For each suspect: git-tracking
count, references from canonical code, all-time history, owner, emptiness.

## Buckets

### Bucket A — Obsolete / duplicate (safe to remove)

| Path | Evidence | Action |
|------|----------|--------|
| `.agents/` | Empty dir. **Never tracked** (no all-time history). Zero references in canonical code. The plural/extra-dot near-duplicate of the canonical `.agent/` rulebook — a merge stub. | `rmdir` (untracked → no `git rm`, no history to preserve). |
| `.codex/` | Empty dir. **Never tracked.** Zero references. AGENTS.md mentions a *future `CODEX.md` redirect file* — not this directory; the dir is an empty merge stub. | `rmdir`. |

No **tracked** path is dead. `git ls-files` finds no committed `__pycache__`,
`*.pyc`, `.DS_Store`, cache dir, or `.coverage` anywhere — nothing to `git rm`.

### Bucket B — Debris (tool caches / artifacts)

All present at root, **all untracked** (none in git). Risk is only a future
accidental `git add`. Several are **not** in `.gitignore` — the gap is the fix.

| Cache | In `.gitignore`? | Action |
|-------|------------------|--------|
| `.mypy_cache/` | ❌ missing | add pattern |
| `.pytest_cache/` | ❌ missing | add pattern |
| `.ruff_cache/` | ❌ missing | add pattern |
| `.hypothesis/` | ❌ missing | add pattern |
| `.import_linter_cache/` | ❌ missing | add pattern |
| `.coverage` | ✅ (line 13) | none |
| `.venv/`, `.uv-python/` | ✅ (lines 8–9) | none |

### Bucket C — Reference (already handled; keep in place)

| Path | State | Action |
|------|-------|--------|
| `Vincent's Code/` | Gitignored (line 38), 0 tracked, owned by `matthieu`. Still a live harvest source (last harvest `03fc3e8`). | Keep. On-disk removal is a `matthieu`/admin step — **not** this audit's. README banner queued for `matthieu`. |
| `ThomasOssen/` | Gitignored (line 34), 0 tracked. | Keep, stays ignored. |
| `Test Lenny/` | Tracked (13 files), README already flags it ignore-me. Imported by nothing. | Keep in place. Admin removes if/when desired. |

### Bucket D — Canonical / live working dirs (keep — not debris)

Each is tracked, referenced by canonical code, and recently committed.

| Path | Why it stays |
|------|--------------|
| `configs/` | The six C7 Part VII YAML bundles (`broker/environment/pricing/qc/scenarios/universe.yaml`); read by `packages/core/.../config/loader.py` and the frontend config API. Canonical. |
| `scripts/` | Operator/export scripts; referenced from `pyproject.toml` and broker `samples/`. |
| `notebooks/` | The four broker demos just rewired to `algotrading.infra` (`00fd709`) + pedagogy notebooks. |
| `research/`, `data/` (README only) | Tracked working dirs; `data/**` blobs already gitignored. |
| `.claude/` | Tracked shared skills (10 SKILL.md). Structural, like `.agent/`. `settings.local.json` already gitignored. |
| `documentation/`, `tasks/`, `.agent/`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `BIG_PICTURE.md`, `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore` | Canonical rulebook/docs/build. |

### Bucket E — Human-decision

None. Every suspect path classified from evidence.

## Near-duplicate adjudication

- `.agent/` (canonical rulebook — every `AGENTS.md` hop references it) **vs**
  `.agents/` (empty stub, never tracked) **vs** `.codex/` (empty stub). Canonical
  is `.agent/`; the other two are removable debris. No `agent/` vs `agents/`
  collision elsewhere.

## Applied patch (safe subset)

1. `.gitignore`: added the five missing cache patterns (Bucket B).
2. `rmdir .agents/ .codex/` (Bucket A) — untracked empty stubs, zero history loss.
   *If owner-permission blocks the rmdir, it is logged as an admin step and the
   dirs stay; nothing references them so the gate is unaffected.*

No tracked path removed (none qualified). No path under `packages/`/`apps/`
touched. No silent deletions — every action traces to a row above.
