# H1 — Repo-hygiene audit: classify the post-merge debris, move nothing until reviewed

- **Owns:** the whole working tree at the root level — directory/file *classification only*. No
  ownership of any package's internals; this task does not refactor code, it inventories paths.
  Touches `.gitignore` and `tasks/TASKBOARD.md` when the audit lands. Any actual deletion of
  `Vincent's Code/` is a `matthieu`-only / admin step (permissions), not this task's.
- **Depends on:** **C7 landed.** Run after config hardening so the audit classifies the *settled*
  tree, not one mid-migration. Also depends on the in-flight broker/notebooks migration being done
  (a scan over a moving tree gives stale results).
- **Blocks:** nothing. Pure hygiene; the gate is already green without it.
- **State going in:** two projects (this repo + Vincent's independent AlgoTrading build) were merged
  toward a max-union, leaving debris: near-duplicate dirs, stray tool caches, and dead paths. The
  canonical tree is `packages/` + `apps/`; everything else is suspect until classified.

## Objective

A reviewed, evidence-backed classification of every non-canonical path at the repo root, produced
**read-only first**. Nothing is moved or deleted until the report is reviewed and approved. Output:
a short report (categories below) + a follow-up patch that only touches `.gitignore` and removes
genuinely-dead paths, applied after sign-off.

## Decisions already taken (do not relitigate)

- **`Test Lenny/` and `Vincent's Code/` stay in place**, each flagged by a README banner (already
  done for Test Lenny; queued for `matthieu` on Vincent's Code). Their on-disk removal is the
  admin's call, **not** this audit's. The earlier "move them into a gitignored `Trash/`" idea was
  dropped in favour of in-place README banners — simpler, and git history is the real safety net.
- `Vincent's Code/` and `ThomasOssen/` are already gitignored and not canonical; the audit confirms
  they stay ignored, it does not re-include them.

## What to do (ordered)

### Task 1 — Read-only inventory *(no mutations)*
1. Walk the root. For every non-canonical path (everything outside `packages/`, `apps/`, and the
   `.agent/` rulebook), record: is it tracked by git? does anything `import`/reference it? last
   commit touching it? owner/permissions? size?
2. Resolve the **near-duplicates** explicitly. Known candidates to adjudicate: `.agent/` (canonical
   rulebook — keep) vs `.agents/` vs `.codex/`; any `agent/` vs `agents/` collision; duplicate
   config or notebook dirs left by the merge. State which is canonical and why for each pair.

### Task 2 — Classify every suspect path into exactly one bucket
3. **Obsolete / duplicate** — superseded by a canonical equivalent; safe to remove. Cite the
   replacement path.
4. **Debris** — tool caches and build artifacts (`.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`,
   `.hypothesis/`, `.import_linter_cache/`, `.coverage`, `__pycache__`, `.venv` strays). Check each
   against `.gitignore` — several caches are **not** currently ignored; flag the gaps.
5. **Reference (already handled)** — `Vincent's Code/`, `ThomasOssen/`, `Test Lenny/`: gitignored
   and/or README-flagged, kept in place. No action beyond confirming the banner/ignore is present.
6. **Human-decision** — anything whose status the audit cannot settle from evidence (a dir an
   author may still want). List with the open question; do **not** guess.

### Task 3 — Land the report, then (after sign-off) the safe patch
7. Write the classification as the report. Get it reviewed.
8. After approval: add the missing cache patterns to `.gitignore`; `git rm` only the paths in
   bucket 3 that are tracked (history preserves them); leave reference/human-decision paths alone.
   `Vincent's Code/` removal stays a `matthieu`/admin step.

## Verification

- The audit ran over a **settled** tree (C7 merged, migration done) — note the commit it ran
  against, so a later reader knows the snapshot.
- Root gate still green after the `.gitignore` + removal patch:
  `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.
- `git status` clean; no canonical path under `packages/`/`apps/` was touched.
- Every path moved/removed traces to a bucket-3 line in the approved report — **no silent
  deletions**. Anything dropped for size/scope is logged, not omitted.

## Done criteria

A reviewed report classifying every non-canonical root path; `.gitignore` covers all tool caches;
dead tracked paths removed (history intact); `Test Lenny/`, `Vincent's Code/`, `ThomasOssen/` left
in place and correctly flagged; gate green; board entry cleared.

## Gotchas

- **`.agent/` is structural** — it is the canonical rulebook every `AGENTS.md` hop references. Never
  touch it. The suspect is `.agents/` (note the plural/extra dot), not `.agent/`.
- **Read-only first.** The whole point is to classify before moving; a scan that deletes as it goes
  defeats the review step and is how useful files vanish silently.
- **Permissions bite.** `Vincent's Code/` is owned by `matthieu`; a `vincent`-run `rm` is
  permission-denied (this is why C5's removal stalled). Hand owner-restricted deletions to the admin.
- **Don't cut a live source.** `Vincent's Code/` is still being harvested from (commit `03fc3e8`);
  confirm everything useful has been ported before treating it as inert.
