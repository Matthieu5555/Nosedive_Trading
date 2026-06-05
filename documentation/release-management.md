# Release management

## The rule

Every change that can move a number ships a release artifact. If a change alters what
the analytics compute — the forward estimator, the IV solver, the surface fit, the
pricer, the risk or scenario math, or any economics in `configs/default.toml` — it does
not land without a short, written record of what changed, why, which tests passed, and
which historical periods were revalidated. A change that only touches plumbing
(logging, a runbook, a test helper, a rename with no behavior change) does not need one.

The reason is the platform's whole premise: every number traces to the exact code and
config that produced it, and a historical result stays reproducible. The config already
enforces half of this — the four sections of `PlatformConfig` are independently
versioned and feed a `config_hash` that is stamped into every derived record, so a
number always points back at the economics that judged it. The release artifact is the
human-readable other half: the prose that says *what the version bump means* and *what
proof was run before it shipped*.

## What "economics-affecting" means here

Concretely, you need a release artifact if your change touches any of:

- the math in `backend/src/forwards`, `backend/src/iv`, `backend/src/surfaces`,
  `backend/src/pricing`, or `backend/src/risk`;
- the actor's valuation join or any projection in `backend/src/actor` that changes a
  persisted value;
- the QC thresholds or any check's verdict logic in `backend/src/qc`;
- any value in `configs/default.toml` (and therefore a section `version` bump).

If you are unsure whether a change moves a number, the cheap test is the byte-identical
replay: run a fixed historical day before and after your change and diff the outputs. If
they differ, it is economics-affecting and needs an artifact. If they do not, it is
plumbing.

## How to revalidate

"Revalidated" is not a feeling, it is a command. For an economics change you:

1. Run the full gate: `cd backend && uv run ruff check . && uv run mypy . && uv run pytest -q`.
   It must be green.
2. Bump the affected config section `version` (so the `config_hash` changes and old and
   new results are distinguishable).
3. Restate the chosen historical periods under the new version, leaving the old numbers
   intact, with `reconstruct_range(..., version="<release-tag>")` (see the
   [replay/backfill runbook](runbooks/replay-backfill.md)).
4. Compare the restatement to the prior live numbers and record what changed and by how
   much. `compare_replay_to_live` names the diverging tables and keys; for an intended
   change you expect divergence and you record its size, not zero.

The versioned restatement is exactly why versioned partitions exist (ADR 0007, decision
3): a new-code run lands *beside* the old analytic, so you can show the before and after
side by side instead of overwriting the evidence.

## The artifact template

Keep it short — a few lines per field is enough. Store it wherever the team keeps
release notes; the point is that it exists and is findable from the version tag.

```
Release: <release-tag, e.g. 2026.06-recalib>
Date: <YYYY-MM-DD>
Author: <name>

What changed:
  <the economics that moved — which module/config, in one or two sentences>

Why:
  <the reason — a bug, a recalibration, a new method>

Config:
  <which section version(s) bumped; old hash -> new hash if known>

Tests passed:
  <gate status: ruff/mypy/pytest pass count; plus any new tests added>

Periods revalidated:
  <the date range(s) restated, the version tag used, and the observed
   difference vs the prior live numbers — magnitude per affected table, or
   "no change" if the restatement matched>
```

A change that ships without its artifact is not done, the same way code without tests is
not done. The artifact is the thing that lets the next engineer — or a future you —
trust that a number from six months ago still means what it says.
