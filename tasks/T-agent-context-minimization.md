# T-agent-context-minimization — strip `.agent/` to the minimum vital, kill context pollution

> **Owner ruling 2026-06-13.** Agents are being confused by too much guidance. The **domain**
> reference is already complete and superior: the **blueprint** (`documentation/blueprint/**`,
> ADR 0011) + the **course transcript**
> (`documentation/transcripts/AlgoTradingCourse2-Greeks-et-strategies-vol.md`). `.agent/` must
> therefore hold **only important, useful RULES + routing** — never a second copy of domain
> knowledge, never merge-era archaeology. Minimum vital, so a fresh agent orients fast and is not
> drowned. **Git is the archive; ADRs are never deleted — superseded/merge-only ones move OUT of
> the default read path (an `archive/` subfolder), they are not erased.**

## Why (the diagnosis)

`.agent/` is **433 lines of root files + 3606 lines across 42 ADRs (~4040 lines total)**. The bulk
— and the confusion — is the ADR sprawl: an agent triaging "what's true now" must wade through
merge-convergence records, superseded wiring decisions, and removed-broker machinery, all sitting
at the same `accepted` weight as live operational rules. The domain it actually needs is in the
blueprint + transcript. So the fix is **subtractive**: move dead weight out of the read path, keep
a thin live set, and stop duplicating what the blueprint/transcript already say.

This task is **both** a cleanup spec **and** a mini-audit of the *same error class* (context
pollution / stale-scope drift) wherever else it appears.

## Scope guard (shared tree — several agents live)

Stage by **explicit path**, never `git add -A`. Do **not** touch: the delta-grid agent's files
(`surfaces/projection.py`, `actor/driver.py`, `configs/qc.yaml`, `test_analytics_projection.py`),
the front-style agent's files (`chartTheme.ts`, `charts.tsx`, `index.css`), or any ADR/`open-questions.md`
another actor holds uncommitted (check `git status -s .agent/` first; ADR 0042 and the 8 amended
ADRs were Matthieu's lane). The roadmap (strategy) is a separate deliverable living **outside**
`tasks/` (in `documentation/`) — not this task.

## Part A — `.agent/` minimum-vital refactor

**Principle:** `.agent/` = *process rules + routing + decisions genuinely not derivable from
blueprint/transcript/code*. If the blueprint or transcript already says it, link — don't restate.

1. **`decisions/` — the main lever. Split, don't delete.**
   - Add `decisions/archive/` and move the **superseded / merge-process / dead-machinery** ADRs
     there (history preserved, out of the default read path). High-confidence archive set (verified
     this session, zero live load):
     **0013** (deribit, superseded), **0014** (saxo, superseded), **0020** (actor wiring, superseded
     by 0023), **0022** (M5 vendored slice, reversed by 0023/0042).
   - Review-then-archive (merge-convergence archaeology — the merge is **closed**; confirm each
     still has no live load before moving): **0007** (decision 1 superseded by 0023), **0008**
     (superseded by 0024/0025), **0016** (EventSource — YAGNI, **0 code refs**), **0018** (M0
     keystone), **0021** (M2 analytics merge). Keep any whose *frozen-seam* clause is still
     enforced by import-linter/tests — if so, fold that one clause into the live ADR that cites it.
   - **Add `decisions/README.md` — a one-line index of the LIVE ADRs only** (number → one-line
     current rule), with a short "Archived (history): see `archive/`" footer. This is the single
     highest-leverage change: an agent reads a ~30-line index, not 3606 lines. Keep it generated/
     curated so it cannot drift (a test already guards doc links).
   - Live set to keep in `decisions/` (operational rules the blueprint does not pin): the analytics/
     risk/storage/config/scheduling/ingestion ADRs **0001–0006, 0009–0012, 0015, 0017, 0019,
     0023–0042** (minus any moved above). None contradict the blueprint (verified this session).

2. **`glossary.md` (208 lines) — cut to project-specific, live, non-blueprint terms only.**
   The blueprint owns the domain glossary (`10-glossary.md`) + data dictionary (`09-`). Keep in
   `.agent/glossary.md` ONLY terms an agent won't infer **and** that the blueprint doesn't already
   define (identity/provenance keys, the as-of/look-ahead boundary, the seam protocol names still in
   code). **Drop** anything the blueprint/transcript defines, and the merge-era M4/M5 workstream
   vocab for seams no longer in code (e.g. `BrokerTransport`/`EventSource` show **0 code refs** —
   demote or drop). The Saxo/Deribit-specific sections were already removed (this session).

3. **`map.md` (26 lines) — keep (routing is vital), trim the one bloated cell.** The Monorepo cell
   is a paragraph; reduce it to a pointer ("the layered uv-workspace; each module's `README.md` is
   the next hop") and let the per-dir READMEs carry detail. Already refreshed to IBKR-only + ADR 0042.

4. **`conventions.md` (110) / `voice.md` (44) — keep (these ARE the useful rules).** Audit only for
   stale-scope lines; both scanned clean of multi-broker refs this session.

5. **`open-questions.md` (45) — already minimal (1 open: OQ-10).** Verified against blueprint +
   transcript: **neither resolves OQ-10** (market-qualified keying for cross-index ticker
   collisions) — it stays open but is **dormant** (SPX parked, SX5E-only). OQ-0…0Q-9 are already
   Resolved with ADR links. No change needed beyond the index-only note already added.

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

Per the transcript (§8–9, dispersion: buy ATM straddles on the **top-10 constituents**, hedge the
index) and the owner's correction: **single names DO become option underlyings — in the dispersion
phase.** The invariant is not "never an underlying"; it is **registry-driven, never hand-set**:

> **Universe model:** one enabled index (SX5E) + its **top-N constituents**, all sourced from the
> registry. The **index** carries an option chain **today** (analytics phase); the **top-N
> constituents** carry theirs **at the dispersion phase** (transcript §8). Any constituent that
> becomes an option underlying is chosen from the **enabled index's top-N**, never a hand-maintained
> list.

Encode this in ADR 0035 (registry) + ADR 0042 (scope) — **owner/Matthieu's lane**: those two files
are held uncommitted by him; flag the nuance to him rather than editing directly. Do **not** write
"never an option underlying" anywhere as an absolute.

## Acceptance

- `.agent/decisions/` active set is the live ADRs only, with a one-line `README.md` index; superseded/
  merge-only ADRs are in `decisions/archive/` (moved, **not deleted**; git history intact).
- `.agent/glossary.md` carries no term the blueprint/transcript already defines and no dead-seam vocab.
- No `.agent/` file restates blueprint/transcript domain content; each instead links to it.
- Part-B pollution items fixed or filed (the code-default one coordinated with the owning lane).
- The universe-model rule (Part C) is encoded as the **registry-driven, index-now/constituents-later**
  nuance — never the "never" absolute.
- Gate green — in particular `packages/infra/tests/test_doc_freshness.py` (no dead relative link after
  ADR moves: fix every `[[NNNN-…]]`/path referent of a moved ADR, or keep the link valid by pointing
  at `archive/`). Staged by explicit path; nothing outside the lane touched.

## Done criteria

A fresh agent opening `.agent/` reads a thin routing map, a short index of live decisions, the house
rules, and a lean glossary — and is pointed at the blueprint + transcript for everything domain. No
agent has to read 3606 lines of ADRs or discount removed-broker machinery to learn "what is true now."
