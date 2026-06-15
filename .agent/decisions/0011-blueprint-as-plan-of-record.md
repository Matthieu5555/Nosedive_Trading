# 0011 — Plan of record: domain authority and scope

- **Status:** accepted
- **Date:** 2026-05-30
- **Source:** Vincent's ADR-009; merged into our stream 2026-06-05

> **AMENDED 2026-06-15 (c) — `documentation/` is gone; `TARGET.md` is the sole domain authority.**
> An earlier amendment (2026-06-13 b) reverted a deletion and stated that `documentation/blueprint/`
> and `TARGET.md` were "two complementary authorities." The owner ruling (2026-06-14, repeated to ~15
> agents) supersedes that: **the `documentation/` tree is outdated and abandoned; it has been
> removed; git history is the archive.** `TARGET.md` absorbed the retired blueprint and is now the
> **sole domain authority** — formulas, contracts, naming, vocabulary, the build sequence, and the
> current scope rulings. For course pedagogy (Greek intuition, strategy rationale, vol-surface
> teaching), the canonical live reference is `ThomasHossen/MM_options_trading.md`. The body below is
> the historical "why we dissolved the V1/V2 split" record and is preserved for context only.
>
> **Current conflict resolution:** `TARGET.md` governs on all domain questions. ADR 0042
> (index-options-only, IBKR sole broker, SX5E sole live index) is a frozen scope ruling encoded in
> `TARGET.md §0`. `AGENTS.md` governs process. Do not read or write `documentation/`.

> **AMENDED 2026-06-13 (b) — superseded by amendment (c) above.** Noted here for history only:
> Vincent restored `documentation/` after an earlier deletion; at that point the blueprint and
> `TARGET.md` coexisted. That state is now superseded — `documentation/` is gone.

## Context

The project was originally scoped as "V1 = steps 1–9 only, live connectivity deferred." After a
full read of the founding blueprint PDF and a multi-agent conformance audit, the split was found
to be the source of observed drift: data contracts were partially filled, naming was misaligned
(e.g. `canonical_ts` vs `snapshot_ts`), and several modules mandated by the blueprint were absent.
The V1/V2 framing was quietly narrowing scope in ways the code did not honor anyway.

The blueprint (*Industrial Roadmap for a Volatility Infrastructure Platform v4.0, 06 April 2026*;
formerly at `documentation/blueprint/`, now absorbed into `TARGET.md` — see amendment (c) above)
is a precise, prescriptive document: it specifies mathematical formulas, data schemas, field
definitions, vocabulary, and a 16-step build order.

## Decision

1. **The blueprint is the plan of record.** It is authoritative on domain (formulas, data contracts,
   data dictionary, naming, vocabulary), scope, and the 16-step roadmap. The code follows it to the
   letter. If the code diverges from the blueprint on anything other than the single sanctioned
   architectural deviation (see below), fix the code — not the doc.

2. **The V1/V2 split is dissolved.** Build in the order of the 16 steps; each step is a milestone.
   Live connectivity is first-class (steps 1 and 3). Replay remains required (step 13).

3. **The single sanctioned architectural deviation is ADR 0001's monorepo layout:** a uv-workspace
   separating `core / infra / strategy / execution` packages, with the frontend as a cross-package
   app (`apps/frontend`). The blueprint permits this explicitly: *"The exact names may vary, but the
   separation of concerns should remain."* Every other blueprint choice is binding.

4. **Conflict resolution within the blueprint:** detailed, prescriptive parts (Part II math, Part III
   roadmap, Part IV implementation guides, Appendix E field-mapping checklist) take precedence over
   the glossary (Part X) and data dictionary (Part IX), which are reading aids, not implementation
   authorities. Among detailed parts, concrete build artifacts — code examples, schemas, pseudocode —
   take precedence over conceptual asides and checklists for field-name choices. If detailed parts
   contradict each other, escalate to the owner; do not decide unilaterally.

5. **`AGENTS.md` wins on process; the blueprint wins on domain.** They govern different things and
   do not conflict. `AGENTS.md` says how to work; the blueprint says what to build and what things
   mean.

## Consequences

One source of truth for domain definitions and scope. Structural drift is prevented by this ADR and
by the steering layer, not by V1/V2 scope fences. The conformance remediation (field alignment,
naming, missing modules) is a prerequisite before the next build step, not a post-launch cleanup.
