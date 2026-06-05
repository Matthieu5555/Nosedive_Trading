# M9 — Discipline layer, docs, and the founding blueprint

- **Branch:** `feat/merge-discipline`
- **Owns:** `AGENTS.md`/`.agent/**`, `documentation/**`, the reconciled steering layer (`.meta/` vs `.agent/`), `.claude/skills/**`, `notebooks/**`, and the founding blueprint.
- **Depends on:** nothing hard — it reconciles steering and docs, lands continuously alongside M0–M8. Touches only top-level/docs, so it is orthogonal to all code workstreams.
- **Blocks:** nothing, but its frozen interface-contract publication supports M0.

## Objective

Merge the two projects' *discipline and knowledge* layers, not their code. Both repos have heavy steering systems that must not coexist unreconciled — ours (`.agent/` + `AGENTS.md`, tool-agnostic) and Vincent's (`.meta/` + `.claude` + the 19-part blueprint, partly French). Land one canonical steering layer plus the union of the genuinely valuable docs.

## What to merge

- **Keep ours as canonical steering:** `AGENTS.md` (single source of truth), `.agent/{map,glossary,conventions,voice}.md`, the append-only ADRs in `.agent/decisions/`, and the `documentation/` ops set (five runbooks, `interface-contracts.md`, `known-limitations.md`, `release-management.md`, and the symlink-mirrored module docs under `documentation/modules/`). Update `map.md` to the new monorepo layout. **Docs layout preference holds:** `documentation/` not `docs/`; one source surfaced in many places via symlink, never copies.
- **Adopt from Vincent (fold in, don't duplicate):**
  - The **founding blueprint** (`packages/infra/docs/blueprint/00..19`) — the canonical domain reference (math framework, data dictionary, acceptance tests, runbooks, glossary). This is depth we lack; bring it in under `documentation/blueprint/` and point `AGENTS.md`/`map.md` at it.
  - The **pedagogical vol-surface doc** + rendered figures + PDF (`docs/vol_surface_pedagogique.*`, `docs/assets/vol_surface/*`) and the **notebooks** (`notebooks/demo_pipeline_{ibkr,saxo,deribit}.ipynb`, `demo_pricing_greeks.ipynb`, `demo_surface_fit.ipynb`). Wire the notebook demos to the merged infra; honor research/as-of reproducibility rules.
  - His decisions/specs/audits/research sessions from `.meta/` — reconcile into our ADR stream where they record a still-live choice; archive the rest as references. Don't keep two ADR numbering systems.
- **Reconcile `.claude/skills`:** keep our disciplined skills (`check-lookahead-bias`, `write-tests`, `review-module-depth`, `python-quality-gate`, `readable-functional-docs`, …); fold in any of Vincent's (`brainstorm`, `debate`, `orchestrate`, `plan`, `research`, `spec`) that don't duplicate ours.
- **Glossary union:** merge his crypto/Deribit and Saxo vocabulary (funding, perpetuals, OAuth scopes) into `.agent/glossary.md`.

## Frozen seam

`AGENTS.md` stays the canonical index; `.agent/map.md` the routing table — updated to the monorepo. The interface-contract list (published with M0) is the frozen-API record. One steering layer, one ADR stream, one glossary.

## Test surface

Read [TESTING.md] first. Docs-heavy, but not test-free:
- Link/freshness check: `map.md` points at directories that exist; `documentation/modules/` symlinks resolve; no dead blueprint links.
- The skills-contract check (M0's CI) passes for the merged skill set.
- Notebooks execute against the merged infra in CI (or are explicitly marked manual with reason), honoring as-of discipline (`check-lookahead-bias`).
- `write-readme`: the root README and each package README reflect the merged reality and the fastest run path.

## Done criteria

One canonical steering layer (`AGENTS.md` + `.agent/`), Vincent's blueprint + pedagogical docs + notebooks folded into `documentation/` without duplication, one ADR stream, a unified glossary and skill set, all links/symlinks resolve, gate + skills-contract green.

## Gotchas

Two steering systems unreconciled is the exact drift `AGENTS.md` exists to prevent — collapse to one, don't staple them together. Don't copy docs; surface one source via symlink (the standing layout preference). The blueprint is reference, not a second rulebook — `AGENTS.md` wins on process, the blueprint wins on domain definitions; say which is which. Translate or summarize the French steering notes so the canonical layer is single-language.
