# H2 — Doc reconciliation: refresh the layered docs to the post-merge stack, guard against re-drift

- **Owns:** the doc layer, not the code. Per-directory `README.md` across `packages/**` + `apps/**`;
  the top hop `.agent/map.md`; `.agent/glossary.md`; the blueprint catalogs
  `documentation/blueprint/09-data-dictionary.md` + `10-glossary.md`; the `documentation/` index and
  its `documentation/modules/` symlink mirror; the root `README.md`. Adds one new lightweight
  doc-freshness check wired into the root gate. **Does not** change `documentation/blueprint/`
  domain content (blueprint overrides — ADR 0011) beyond the data-dictionary/glossary catalog entries.
- **Depends on:** **H1 landed** (don't document debris you're about to delete), **C7 landed**, and the
  in-flight broker/notebooks migration **done**. Documenting a moving tree produces stale docs.
- **Blocks:** nothing structurally. But it's what makes the stack legible to the next agent/human —
  the "three hops" (`map → directory README → code`) only work if the rungs are current.
- **State going in:** the layered system the owner asked for **already exists** and is the house
  design (AGENTS.md "Orient yourself in three hops"; "Keep the docs alive"). What's missing is a
  **reconciliation**: the merge changed real behavior — Nautilus is the runtime spine (ADR 0023),
  the collection seam is push-canonical (ADR 0027), config is YAML→typed→DI (C7 / ADR 0028), three
  broker leaf adapters — so the README ladder, the map, and the catalogs have drifted from the code.
  And the freshness rule is unenforced (convention only), so it re-drifts.

## Objective

Every doc reflects the **settled post-merge behavior**; the bottom-up README ladder is complete and
rolls up coherently (leaf module → package → root README + map); the curated catalogs (data
dictionary, glossary, per-module public interface) are current; and a lightweight automated guard in
the gate stops the ladder from silently re-drifting. Gate green throughout.

## Decisions already taken (do not relitigate)

- **Catalog is curated by hand, not generated.** The data dictionary stays the authoritative field
  catalog; each module README documents its **public interface** (key functions/classes/entry
  points) in prose. No pdoc/sphinx auto-API — exhaustive autogen rots and adds a tool to the gate.
- **Add the automated freshness guard** (see Task 4). The "keep docs alive" per-change rule stays;
  this guard is the mechanical backstop it never had.
- The ladder is the **existing** design — refresh and complete it, don't invent a parallel one.

## What to do (ordered)

### Task 1 — Bottom-up refresh pass (leaf → root)
1. Walk every module README under `packages/infra/src/algotrading/infra/**` (and `core`, the broker
   adapters, `strategy`, `execution`, `apps/frontend`). Each must answer, briefly: **what this
   directory is for**, **what it does** (the mechanics), **its public interface** (the functions/
   classes/entry points a caller uses), **gotchas**, and **how it sits in the layer**
   (`core ← infra ← infra-<broker> ← {strategy,execution} ← frontend`). Fix anything that describes
   pre-merge behavior.
2. Roll the summaries up: each **package** README summarizes its modules; the **root** `README.md`
   summarizes the packages and the new stack shape (Nautilus spine, push collection seam, the three
   adapters, one gate). A reader climbing leaf→root should get a coherent, non-contradictory picture.

### Task 2 — Refresh the curated catalogs
3. **Data dictionary** (`blueprint/09-data-dictionary.md`): reconcile every field/contract against
   the current `infra/contracts` + the typed config bundles (C7). One authoritative definition per
   field; no field defined in two places.
4. **Glossaries** (`.agent/glossary.md`, `blueprint/10-glossary.md`): add terms the merge introduced
   (e.g. runtime-spine/actor vocabulary, push collector seam); remove dead ones.
5. Confirm each module README's **public-interface** section names symbols that actually exist
   (no ghost functions, no missing entry points).

### Task 3 — Refresh the top hops
6. `.agent/map.md`: every top-level area listed, every "start here" pointer correct, nothing
   pointing at a path H1 removed. Keep it a **routing table, not a description** (house rule).
7. `documentation/` index current; the `documentation/modules/` symlink mirror has exactly one live
   link per module README — no broken links, no orphans. **Edit the source README, never the mirror.**

### Task 4 — The freshness guard (gate-wired)
8. Add a lightweight check, collected by the **root `pytest`** (so it runs in the one gate), that
   asserts: every `packages/*` and every `infra/**` module dir has a `README.md`; `.agent/map.md`
   references every top-level area; every `documentation/modules/` symlink resolves; no relative doc
   link in a README/map is dead. Place it where the root pytest collects it; keep it fast and
   dependency-free.

## Verification

- Root gate green **including the new doc check**:
  `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.
- Cold-start spot check: for 2–3 modules, follow `map.md → directory README → code` as a fresh agent
  would and confirm you land in the right place with the right mental model.
- Every public symbol named in a README exists; every data-dictionary field maps to a real contract
  field; no field defined twice.
- The doc ran against the **settled** tree (H1 + C7 + migration done) — note the commit it targets.
- No AGENTS.md rule is restated inside a README (single-source principle; restating is how docs drift).

## Done criteria

The README ladder is complete and current leaf→root; data dictionary + glossaries match the code;
`map.md` and the `documentation/modules/` mirror are accurate and unbroken; the freshness guard is
in the gate and green; a fresh agent can understand the new stack behavior from the docs alone.

## Gotchas

- **Don't restate `AGENTS.md`/conventions in READMEs.** The whole anti-drift design is one rule, one
  home. A README points; it doesn't recopy process rules.
- **Blueprint overrides on domain** (ADR 0011). For formulas/contracts, reconcile *to* the blueprint;
  don't rewrite the blueprint to match drifted code — if code and blueprint disagree, that's a bug to
  raise, not a doc edit.
- **`documentation/modules/` are symlinks.** Edit the README next to the code; the mirror updates for
  free. Editing the mirror desyncs them.
- **Don't autogenerate.** Decision is curated prose; an auto-API reference is explicitly out of scope.
- This is the **catch-up** pass. The per-change "keep docs alive" rule still applies to every future
  change — H2 doesn't replace it, it gives it a clean baseline + a guard.
