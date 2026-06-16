# platform-doc-coherence-fix — quarantine the dead documentation/ tree and re-point every reference

**State: done on branch `worktree-agent-a62613055e43cdb9f` (2026-06-15).** `documentation/` was
already absent from disk. All live references re-pointed. See TASKBOARD claim row for details.

**Owner ruling (2026-06-14, said to ~15 prior agents):** the `documentation/` tree is
**outdated and abandoned**. It is not a source of truth and must not be read or written. Authority
is `TARGET.md` (domain), `AGENTS.md` (process), `.agent/` (map/conventions/decisions/glossary), and
per-directory `README.md`s. `TARGET.md` already says "the documentation/ tree is gone" — correct in
intent; this task makes disk match the ruling and stops tasks pointing agents back into the residue.

- **Owns:** the deletion/quarantine of `documentation/` and the re-pointing of every live reference
  to it. Doc-only; touches no code or config.
- **Depends on:** nothing — the ruling is made. Coordinate the *deletion* with the owner (it is a
  destructive, hard-to-reverse tree removal on a shared checkout) but the *direction* is settled.
- **State going in (audited 2026-06-14):** the tree exists on disk
  (`blueprint/`, `connectivity/`, `transcripts/`, `vol-surface/`) and **11 active task files still
  reference it**, several *instructing agents to read it* — which is exactly why agent after agent
  re-asks "is documentation/ authoritative?". The references break into two kinds, handle each:
  - **Stale infra/plan residue** — `blueprint/*`, `connectivity/server-deployment-plan.md`. Superseded
    by `TARGET.md` / `.agent/` / `scripts/systemd/` reality. Delete; re-point to the live source.
  - **Course material** — `transcripts/AlgoTradingCourse2-*.md`, `vol-surface/*.md`. TARGET §3 cites
    the course via `ThomasHossen/MM_options_trading.md` (repo root), so the *content* is still live —
    confirm the canonical copy is the `ThomasHossen/` one and re-point the `documentation/transcripts/`
    citations there, then drop the duplicate. Do not lose the pedagogy; relocate the pointer.

## What to do (ordered)

1. **Re-point the references.** Sweep `grep -rl "documentation/" tasks/ TARGET.md .agent/` and fix
   each: blueprint/connectivity → `TARGET.md` §/`configs/`/`scripts/systemd/`; course transcripts →
   `ThomasHossen/`. Known offenders to start from: `platform-intent-vs-delivery-audit` (reads
   `documentation/blueprint/`), `platform-deploy-stack-ownership` (`connectivity/`),
   `ibkr-clock-timer-coherence` (points the systemd units at `documentation/connectivity/` — they
   live in `scripts/systemd/`), `infra-rt-vega`, `infra-mirror-greeks-putcall`,
   `strategy-delta-hedge-band`, `ibkr-option-volume-capture`, `frontend-sigfig-scientific-display`,
   `T-agent-context-minimization`.
2. **Make `TARGET.md` unambiguous.** Its "is gone" lines are correct in intent but contradict disk;
   once the tree is actually removed they become true — keep them, and remove the parenthetical that
   implies the tree is the live archive (git history is).
3. **Delete/quarantine the tree.** After all live references are re-pointed and the course pointers
   relocated, remove `documentation/` (owner-gated destructive step on the shared checkout). Git
   history remains the archive.

## Done criteria

`grep -rl "documentation/" tasks/ TARGET.md .agent/` returns nothing live (or only an intentional
"this tree was removed" note); course pedagogy is reachable from `ThomasHossen/`; the `documentation/`
tree is deleted or clearly quarantined; no agent has a task that sends it back into the residue. No
code or config touched.

## Gotchas

- **Re-point before you delete.** Deleting first orphans 11 tasks' references; fix the pointers, then
  remove the tree.
- **Don't drop the course content.** `transcripts/` + `vol-surface/` carry the prof's teaching several
  strategy/infra specs cite — relocate the canonical copy (`ThomasHossen/`) and re-point, don't just rm.
- **The deletion is destructive and on a shared checkout** — owner-gate that step; the re-pointing is safe to do now.
