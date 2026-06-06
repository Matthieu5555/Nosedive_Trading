# TASKBOARD

The in-repo collision guard for a shared workspace with several humans and agents
working at once. **Before you start changing files, claim them here.** Clear your
claim when you're done. This is advisory, not enforced — it only works if every
actor reads and writes it, which is why `AGENTS.md` tells you to. What we actually
care about is the **working tree on the server staying clean**: everything canonical
under `packages/` and `apps/`, one tree, one gate. Branches are optional convenience,
not the goal.

When a task is finished, clear its row. The record of *what* was built and *why*
lives in the code, the per-directory READMEs, and the ADRs in `.agent/decisions/`;
finished task specs move to [`tasks/archive/`](archive/).

## Current phase: build the index options-analytics pipeline

> **▶ START HERE: [`documentation/roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md)
> is the plan of record for what we build next.** It sequences the work Phase 0 → Phase 3:
> index → point-in-time constituents → delta-band option chain → IV / surface / Greeks
> (decimal + dollar) → daily-close cron → operator front page, then Tab-2 risk/strategy, then
> an execution sketch. Read [`vision-medium-term.md`](../documentation/vision-medium-term.md)
> for the *why* and [`.agent/open-questions.md`](../.agent/open-questions.md) for the decisions
> behind it (OQ-1…6 are all ruled). The blueprint (`documentation/blueprint/`, ADR 0011) still
> overrides on any domain/formula conflict.

**Ground truth (2026-06-06):** the full root gate is **green** —
`uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q` →
**805 passed, 16 skipped, 0 failed**, ruff clean, mypy clean (173 files), import-linter 2/2.
This is the **only** gate. There is one tree (`packages/` + `apps/`); the old flat `backend/`
is gone.

### The merge/convergence is closed

This repo and Vincent's independent build of the same system
(`github.com/Vincent-20-100/AlgoTrading`) were merged toward the max-union of both. That work
is **done**: the layered uv-workspace monorepo is the chassis, **Nautilus is the runtime spine**
([ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)), and the
whole system — core, the frozen `contracts` seam, storage, the analytics core, risk, QC/validation,
the Nautilus-hosted actor, orchestration/observability, and the three broker leaf adapters
(IBKR via Nautilus + a custom Client-Portal REST transport; Saxo + Deribit via our own adapters)
— is canonical in `packages/infra` with one frontend in `apps/frontend`. The four headline
acceptance tests (byte-identical replay, provenance, reconstruction, handover) run inside the root
gate. Vincent's repo is checked out read-only at **`Vincent's Code/`** (gitignored, not canonical)
as a source of inspiration; refresh with `git -C "Vincent's Code" pull`.

The convergence tasks **C1–C6 and C8** all landed and merged to `main`; their specs are in
[`tasks/archive/`](archive/) as the historical record, alongside the original A–E backbone and the
M0–M10 merge fan-out. The closed convergence runbook is
[`archive/CONVERGENCE-PLAN.md`](archive/CONVERGENCE-PLAN.md). **What remains from convergence is
C7 only** (config hardening — the standard landed as [ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md),
but most of the application is unbuilt; see below). The IBKR-REST course requirement landed
([ADR 0024](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md) accepted); its evaluation
record is [`archive/ibkr-rest-api-evaluation.md`](archive/ibkr-rest-api-evaluation.md).

## In flight

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| claude/Matthieu | `.env.example`, `scripts/ibkr_bootstrap.py`, `documentation/connectivity/**` | 2026-06-06 | server-deploy plumbing (non-compute); does **not** touch C7 / `core/config/**` — see [tasks/server-deploy-plumbing.md](server-deploy-plumbing.md) |

## What's next — the index-analytics build

The detailed per-workstream `tasks/` specs (C-series style) are written **per phase as Phase 0
closes**, per [`roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md) §6. The
sequence and the one task already specced:

| When | Work | Spec |
|------|------|------|
| **✅ Done (100% — gate open)** | **C7 — config hardening.** Tasks 1–5 **and both carry-forwards** landed: six Part VII YAMLs + bundle loader; every hashed economic param in validated typed config (no `.py` literals at the audited sites); per-bundle `config_hashes` on every stamp; injected code identity + per-run config freeze + `validate_manifest`; broker.yaml bands/backoff wired; and the **effective-dated profile store** on SQLite (`ProfileRepository`/`resolve_as_of`). Both halves locked: replay-a-run **and** replay-a-past-day-fresh. The owner prerequisite — *no new compute until params are in YAML and reproducibility is locked* — is fully met. Ready to archive the spec. | [C7-config-hardening.md](C7-config-hardening.md) |
| **Phase 0** | Pin the tenor grid + $-Greek units/flags; build the IBKR historical-bar fetch (underlying daily OHLC); decide futures capture (ADR + blueprint amendment, or defer). | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 0 |
| **Phase 1 (Tab 1)** | 1A membership → 1B delta-band selection → 1C capture (daily close + history) → 1F (tenor×Δ-band) projection → 1G cron → 1H QC → 1I front page. (1D futures gated, parallel; 1E raw store is a no-op.) | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 1 |
| **Phase 2 (Tab 2)** | Basket builder → stress/scenario (±50% spot/vol) → PnL attribution by Greek → strategy composition. | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 2 |
| **Phase 3 (sketch)** | Execution: ticket → sign (email) → send. Read-only/paper until an explicit owner gate. | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 3 |

Before writing any test, read [TESTING.md](TESTING.md) — the shared test-surface contract and the
converged seam → contract-test map. Code without the named tests is not done.

## Known carried-forward items

- **[H1 — repo-hygiene audit](H1-repo-hygiene-audit.md) — ✅ landed (2026-06-06, against `e0ab3ab`).**
  Read-only classification done; report at [H1-repo-hygiene-report.md](H1-repo-hygiene-report.md).
  Outcome: **no tracked dead paths** (nothing to `git rm`). Applied safe patch — added the five
  missing tool-cache patterns to `.gitignore` (`.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`,
  `.hypothesis/`, `.import_linter_cache/`; all were untracked) and `rmdir`'d the two empty
  never-tracked merge stubs `.agents/` and `.codex/`. `Test Lenny/`, `Vincent's Code/`, `ThomasOssen/`
  left in place and flagged. Gate green. `Vincent's Code/` on-disk removal stays a `matthieu`/admin
  step (see below).
- **[H2 — doc reconciliation](H2-doc-reconciliation.md) — ✅ landed (2026-06-06, against `e0ab3ab`).**
  Report: [H2-doc-reconciliation-report.md](H2-doc-reconciliation-report.md). The **gate-wired
  freshness guard** is in (`packages/infra/tests/test_doc_freshness.py`, 33 cases: README coverage,
  symlink resolution, map routes every area, no dead doc links). Audited all 18 infra-module + 8
  package READMEs; fixed five concrete drifts (four C7 "param is now typed config, not a `.py`
  literal" in forwards/iv/surfaces/risk + execution's layer position). Glossary gained the merge
  terms (config bundle, push collection seam / `RawCollector`). `map.md` verified current; the docs
  index gate command fixed (it omitted `lint-imports`). **Raised, not edited:** the blueprint
  data-dictionary vs code contract field-name split, recorded as
  [OQ-7](../.agent/open-questions.md) for an owner ruling (ADR 0011 — blueprint wins on domain, so
  it's a bug to raise, not a doc edit). Gate green incl. the new check.
- **`Test Lenny/`** is a throwaway standalone experiment (tracked in git, but not canonical and
  imported by nothing). Its README now flags it as ignore-me. Remove in the hygiene pass / by the admin.
- **`Vincent's Code/` removal** was blocked during C5 (the dir is owned by `matthieu`; the C5 process
  ran as `vincent`, so `rm` was permission-denied — the clone is intact). It is gitignored and not
  canonical, so it does not affect the gate; its README should be flagged as reference-only (see the
  banner queued for `matthieu` to apply). Remove it as `matthieu` (then drop its `pyproject`/
  `.gitignore` excludes) whenever convenient. Not blocking.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-06 | short intent |`
