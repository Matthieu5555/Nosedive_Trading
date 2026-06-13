# T-agent-context-minimization — strip `.agent/` to the minimum vital, kill context pollution

> **Owner ruling 2026-06-13 (amended after the `documentation/` deletion).** Agents are being
> confused by too much guidance. The **domain** reference is now **`TARGET.md` (repo root)** — the
> single domain authority; it absorbed the blueprint + course transcript, which were removed with
> the `documentation/` tree (ADR 0011; git history is the archive). `.agent/` must therefore hold
> **only important, useful RULES + routing** — never a second copy of domain knowledge, never
> merge-era archaeology. Minimum vital, so a fresh agent orients fast and is not drowned.
> **Git history is the archive: superseded/merge-only ADRs are UNTRACKED (`git rm`), not kept in an
> in-tree `archive/` folder ("au pire on remonte dans git").**

## Why (the diagnosis)

`.agent/` is **433 lines of root files + 3606 lines across 42 ADRs (~4040 lines total)**. The bulk
— and the confusion — is the ADR sprawl: an agent triaging "what's true now" must wade through
merge-convergence records, superseded wiring decisions, and removed-broker machinery, all sitting
at the same `accepted` weight as live operational rules. The domain it actually needs is in
`TARGET.md`. So the fix is **subtractive**: move dead weight out of the read path, keep
a thin live set, and stop duplicating what `TARGET.md` already says.

This task is **both** a cleanup spec **and** a mini-audit of the *same error class* (context
pollution / stale-scope drift) wherever else it appears.

## Scope guard (shared tree — several agents live)

Stage by **explicit path**, never `git add -A`. Do **not** touch: the delta-grid agent's files
(`surfaces/projection.py`, `actor/driver.py`, `configs/qc.yaml`, `test_analytics_projection.py`),
the front-style agent's files (`chartTheme.ts`, `charts.tsx`, `index.css`), or any ADR/`open-questions.md`
another actor holds uncommitted (check `git status -s .agent/` first; ADR 0042 and the 8 amended
ADRs were Matthieu's lane). The roadmap (strategy) now lives in
`TARGET.md` (repo root), not in `tasks/` — not this task.

## Part A — `.agent/` minimum-vital refactor

**Principle:** `.agent/` = *process rules + routing + decisions genuinely not derivable from
`TARGET.md`/code*. If `TARGET.md` already says it, link — don't restate.

1. **`decisions/` — the main lever. UNTRACK the dead, don't keep an in-tree archive.**
   - **Owner ruling 2026-06-13: untrack (`git rm`), not an `archive/` folder — git history is the
     archive ("au pire on remonte dans git").** Removing a superseded/dead ADR from the tree is the
     intended end state; recover from history if ever needed.
   - **DONE (this session):** untracked **9** ADRs (42 → 33), all orphan-checked (every live clause
     recorded elsewhere) — **0013** (deribit), **0014** (saxo), **0020** (actor wiring → 0023), **0008**
     (live IBKR adapter → 0024/0025 + code read-only invariant), **0016** (EventSource, YAGNI, 0 refs),
     **0021** (M2 merge → frozen pricing in 0004), **0029** (field names → blueprint 09), **0007**
     (integration/ops → no-dual-path in 0025/0027), **0018** (M0 keystone → layering in import-linter +
     map). Gate stays green (no `[](path)` referent in map/READMEs). Also added **`decisions/README.md`** — the one-line index of the 35 live
     ADRs (read it instead of the bodies); glossary trimmed (dead seams + vol-textbook terms → point to
     blueprint/vol-surface).
   - **Dangling-link sweep (cosmetic, gate-green; do when the holding file is free):** markdown
     `[ADR 00NN](…)` links to removed ADRs remain in **held/high-traffic** files I must not clobber —
     `tasks/TASKBOARD.md` (0021, 0029) — and in **archived** specs `tasks/archive/{T-bridge,2A,2B,1I,1F,H2,ibkr-rest-api-evaluation}.md`
     (historical, low value). Active non-held refs were already repointed (`2C-pnl-attribution.md` →
     blueprint 09; `documentation/connectivity/capture-forward.md` → drop the 0016 mention).
   - **DEFERRED:** **0022** (M5 vendored slice, reversed) — Matthieu holds it uncommitted; untrack it
     after his commit (or hand it to him).
   - **Review-then-untrack** (merge-convergence archaeology — the merge is **closed**; confirm each
     has no live load first): **0007** (decision 1 superseded by 0023), **0008** (superseded by
     0024/0025), **0016** (EventSource — YAGNI, **0 code refs**), **0018** (M0 keystone), **0021** (M2
     analytics merge). Keep any whose *frozen-seam* clause is still enforced by import-linter/tests —
     if so, fold that one clause into the live ADR that cites it before untracking.
   - **Add `decisions/README.md` — a one-line index of the LIVE ADRs only** (number → one-line
     current rule), with a short "Removed/superseded ADRs live in git history" footer. This is the single
     highest-leverage change: an agent reads a ~30-line index, not 3606 lines. Keep it generated/
     curated so it cannot drift (a test already guards doc links).
   - Live set to keep in `decisions/` (operational rules the blueprint does not pin): the analytics/
     risk/storage/config/scheduling/ingestion ADRs **0001–0006, 0009–0012, 0015, 0017, 0019,
     0023–0042** (minus any moved above). None contradict the blueprint (verified this session).

2. **`glossary.md` (195 → 116 lines) — DONE: cut to project-specific, live, non-`TARGET.md` terms.**
   Dropped the generic "Quant / finance" textbook section (look-ahead/point-in-time/realized vol — all
   inferable or carried by `TARGET.md`), the `ecogest` "TODO: define" placeholder, and the redundant
   "Feed notice" (kept as `FeedFault`); every multi-sentence entry compressed to a single
   disambiguation line. What remains is only terms where a wrong synonym silently corrupts code
   (identity/provenance keys, the as-of boundary, line-vs-lot, greek scalings, the seam protocol names).

3. **`map.md` (26 lines) — keep (routing is vital), trim the one bloated cell.** The Monorepo cell
   is a paragraph; reduce it to a pointer ("the layered uv-workspace; each module's `README.md` is
   the next hop") and let the per-dir READMEs carry detail. Already refreshed to IBKR-only + ADR 0042.

4. **`conventions.md` (110) / `voice.md` (44) — keep (these ARE the useful rules).** Audit only for
   stale-scope lines; both scanned clean of multi-broker refs this session.

5. **`open-questions.md` (45 → 13 lines) — DONE: gutted to a live-only stub.** The entire Resolved
   table (OQ-0…OQ-9, all duplicating their ADRs) was deleted and the framing slimmed; only the one
   live row survives — OQ-10 (market-qualified keying for cross-index ticker collisions), **dormant**
   (SPX parked, SX5E-only), unresolved by blueprint/transcript. File kept (not deleted) because ADRs
   0028/0035/0036/0037 backlink to it; full deletion would need backlink surgery on those frozen ADRs.

## Part B — mini-audit of the same error class (stale-scope pollution elsewhere)

Same disease, other organs. Findings to fix (each its own small change, disjoint from above):

1. **Stale broker default in CODE (not just docs).** `provider: str = "DERIBIT"` is the default in
   `packages/infra/src/algotrading/infra/collectors/normalize.py:60` and `storage/events.py:53`;
   `orchestration/provider_flow.py:41` still lists `DERIBIT/SAXO/IBKR`. The provider *dimension* is
   correctly generic (ADR 0017) — but the **literal default should be `"IBKR"`** (the sole live
   broker). A `"DERIBIT"` default in an index-only/IBKR-only app is exactly the silent-drift smell.
   *(Code lane — coordinate; not `.agent/`.)*
2. **Stale notebooks.** `notebooks/demo_pipeline_saxo.ipynb`, `demo_pipeline_deribit.ipynb`,
   `demo_pipeline_deribit_v2.ipynb` reference removed packages — dead. Remove or move to a scratch
   area; `map.md` already flags them stale.
3. **Docstring path mentions of `infra-saxo`/`infra-deribit`** in `packages/infra/src` (cosmetic,
   flagged "low-value sweep" in `T-index-only-refactor`) — sweep them to IBKR.
4. **The "single-names are NEVER option underlyings" absolute** (see Part C) — appears in memory and
   risks landing in specs/ADRs. It is **wrong as an absolute** and must be stated as the nuanced rule.

## Part C — the universe-model rule to ENCODE (do not over-narrow)

Per the dispersion strategy (buy ATM straddles on the **top-10 constituents**, hedge the
index — see `TARGET.md`) and the owner's correction: **single names DO become option underlyings —
in the dispersion phase.** The invariant is not "never an underlying"; it is **registry-driven,
never hand-set**:

> **Universe model:** one enabled index (SX5E) + its **top-N constituents**, all sourced from the
> registry. The **index** carries an option chain **today** (analytics phase); the **top-N
> constituents** carry theirs **at the dispersion phase**. Any constituent that
> becomes an option underlying is chosen from the **enabled index's top-N**, never a hand-maintained
> list.

Encode this in ADR 0035 (registry) + ADR 0042 (scope) — **owner/Matthieu's lane**: those two files
are held uncommitted by him; flag the nuance to him rather than editing directly. Do **not** write
"never an option underlying" anywhere as an absolute.

## Acceptance

- `.agent/decisions/` active set is the live ADRs only, with a one-line `README.md` index; superseded/
  merge-only ADRs are **untracked** (`git rm`; recoverable from git history), not kept in-tree.
- `.agent/glossary.md` carries no term `TARGET.md` already defines and no dead-seam vocab.
- No `.agent/` file restates `TARGET.md` domain content; each instead links to it.
- Part-B pollution items fixed or filed (the code-default one coordinated with the owning lane).
- The universe-model rule (Part C) is encoded as the **registry-driven, index-now/constituents-later**
  nuance — never the "never" absolute.
- Gate green — in particular `packages/infra/tests/test_doc_freshness.py` (it checks only `[](path)`
  links in map.md + READMEs, not `decisions/*` cross-links nor `[[wiki]]` refs, so untracking an ADR
  is gate-safe provided no map/README `[](path)` points at it; sweep dangling `[[…]]` text when the
  holding actor commits). Staged by explicit path; nothing outside the lane touched.

## Done criteria

A fresh agent opening `.agent/` reads a thin routing map, a short index of live decisions, the house
rules, and a lean glossary — and is pointed at `TARGET.md` for everything domain. No
agent has to read 3606 lines of ADRs or discount removed-broker machinery to learn "what is true now."
