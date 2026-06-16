# Founding blueprint — faithful transcription

This folder is a block-by-block Markdown transcription of the founding source document:

> `docs/1780037915_industrial_roadmap_volatility_infrastructure_v4.pdf`
> *Industrial Roadmap for a Volatility Infrastructure Platform — Version 4.0, 06 April 2026.*

One Markdown file per Part of the original (19 Parts + front matter). Formulas are
transcribed in LaTeX (`$$…$$` / `$…$`); tables and code are preserved verbatim.

---

## Governance status — READ THIS FIRST

This blueprint is the project's **canonical domain reference**. It is authoritative on
*what things mean and how they are defined*: mathematical formulas, data contracts, the
data dictionary, field definitions, naming, and vocabulary.

**It is ALSO the project's plan of record (ADR-009, 2026-05-30).** It is followed **to the
letter** — domain, scope, and the full 16-step roadmap. There is **no V1/V2 split**; build in
blueprint step order, each step a milestone.

The steering layer records invariants and the single sanctioned deviation; it no longer narrows
scope:

- `AGENTS.md` — canonical process rules; the single source of truth for how to work in this repo.
- `.agent/decisions/` — ADRs; see **ADR 0011** (blueprint as plan of record), which codifies this
  governance and supersedes any earlier V1/V2 scope framing.
- `.agent/map.md` — routing table to every directory and its README.

**The only sanctioned deviation is architectural** (ADR 0001): a uv-workspace monorepo separating
the `core` / `infra` / `strategy` / `execution` packages, with the frontend as a cross-package app
(`apps/frontend`) — permitted by the blueprint's own *"the exact names may vary, but the separation
of concerns should remain."* Everything else follows the blueprint; if the code diverges from it on
anything other than that architectural exception, **fix the code, not the doc**.

---

## Contents

| File | Part | Topic |
|---|---|---|
| [`00-overview.md`](00-overview.md) | — | Cover, document overview, deliverables, 16-step roadmap at a glance |
| [`01-architecture.md`](01-architecture.md) | I | System architecture and engineering principles |
| [`02-math-framework.md`](02-math-framework.md) | II | Mathematical framework (Equations 1–23) |
| [`03-roadmap-16-steps.md`](03-roadmap-16-steps.md) | III | Sixteen-step implementation roadmap (Steps 1–16) |
| [`04-implementation-guides.md`](04-implementation-guides.md) | IV | Detailed implementation guides (incl. Eq 24, MAD z-score) |
| [`05-math-notes.md`](05-math-notes.md) | V | Expanded mathematical notes (Eq 25) |
| [`06-runbooks.md`](06-runbooks.md) | VI | Operational runbooks |
| [`07-configuration.md`](07-configuration.md) | VII | Configuration design and example artifacts |
| [`08-acceptance-tests.md`](08-acceptance-tests.md) | VIII | Acceptance tests by module |
| [`09-data-dictionary.md`](09-data-dictionary.md) | IX | Data dictionary |
| [`10-glossary.md`](10-glossary.md) | X | Glossary |
| [`11-handover-checklist.md`](11-handover-checklist.md) | XI | Junior handover checklist |
| [`12-file-by-file-guide.md`](12-file-by-file-guide.md) | XII | File-by-file implementation guide |
| [`13-appendices.md`](13-appendices.md) | XIII | Extended appendices |
| [`14-slos-monitoring.md`](14-slos-monitoring.md) | XIV | SLOs, monitoring, operational metrics |
| [`15-data-governance.md`](15-data-governance.md) | XV | Data retention, lineage, governance |
| [`16-test-matrix.md`](16-test-matrix.md) | XVI | Extended test matrix |
| [`17-coding-examples.md`](17-coding-examples.md) | XVII | Extended coding examples |
| [`18-misunderstood-points.md`](18-misunderstood-points.md) | XVIII | Frequently misunderstood implementation points |
| [`19-final-reminders.md`](19-final-reminders.md) | XIX | Final implementation reminders (+ Appendix E field mapping) |

---

## Transcription method & fidelity

- Each Part was transcribed by reading the **rendered PDF pages as images** (not raw text
  extraction), to preserve formula fidelity. The PDF page is the source of truth.
- A **second, independent verification pass** re-rendered every page and re-checked each file
  line by line (equations, tables, code blocks, directory trees, list items). Discrepancies
  found and corrected: an equation parenthesization (Eq 19), a few prose/value fixes in the
  Part IV implementation guides (incl. "loose pandas rows", repository tree), and two formula
  fixes in the appendices (≈ vs =, brackets vs bars). All 25 numbered equations and the YAML
  config snippet were confirmed value-by-value.
- **Residual caveat (honest):** the verification was thorough but is not a formal proof.
  Part III (the 16-step roadmap) is prose-only and omits the inline equations shown in the
  PDF — those equations are fully captured in Parts II, IV, V, and XIII. One glyph remains
  ambiguous (Eq 23 index subscript, kept as uppercase `I`). For any load-bearing detail,
  check the source PDF page cited at the top of each file.
