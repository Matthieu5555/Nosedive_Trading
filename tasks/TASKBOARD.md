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
| — | — | — | _No active claims._ |

## What's next — the index-analytics build

The detailed per-workstream `tasks/` specs (C-series style) are written **per phase as Phase 0
closes**, per [`roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md) §6. The
sequence and the one task already specced:

| When | Work | Spec |
|------|------|------|
| **Now (only live convergence remnant)** | **C7 — config hardening.** Standard ratified (ADR 0028); Task 1 (typed config over a YAML overlay) landed additively. **Tasks 2–5 remain:** the six base YAMLs, retire the TOML path, the reflective `from_config` builder + per-domain wiring at one entrypoint, and the reproducibility hardening (per-bundle `config_hashes`, manifest freeze, effective-dated profiles). Owner's stated prerequisite for *adding* the index pipeline: no new compute until params are in YAML and reproducibility is locked. | [C7-config-hardening.md](C7-config-hardening.md) |
| **Phase 0** | Pin the tenor grid + $-Greek units/flags; build the IBKR historical-bar fetch (underlying daily OHLC); decide futures capture (ADR + blueprint amendment, or defer). | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 0 |
| **Phase 1 (Tab 1)** | 1A membership → 1B delta-band selection → 1C capture (daily close + history) → 1F (tenor×Δ-band) projection → 1G cron → 1H QC → 1I front page. (1D futures gated, parallel; 1E raw store is a no-op.) | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 1 |
| **Phase 2 (Tab 2)** | Basket builder → stress/scenario (±50% spot/vol) → PnL attribution by Greek → strategy composition. | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 2 |
| **Phase 3 (sketch)** | Execution: ticket → sign (email) → send. Read-only/paper until an explicit owner gate. | [roadmap §3](../documentation/roadmap-index-analytics.md) Phase 3 |

Before writing any test, read [TESTING.md](TESTING.md) — the shared test-surface contract and the
converged seam → contract-test map. Code without the named tests is not done.

## Known carried-forward items

- **Repo-hygiene audit (queued, after C7).** The two merged projects left debris worth sweeping
  once C7 lands: obsolete/duplicate dirs (e.g. `.agent/` canonical vs `.agents/`, `.codex/`),
  stray tool caches, and dead paths. Do it as a **read-only classification first** — tag every
  suspect path as obsolete / debris / reference / human-decision and get the report reviewed
  *before* moving anything. Decision so far: `Test Lenny/` and `Vincent's Code/` **stay in place**,
  each flagged by its own README banner; their on-disk removal is the admin's call, not the audit's.
- **`Test Lenny/`** is a throwaway standalone experiment (tracked in git, but not canonical and
  imported by nothing). Its README now flags it as ignore-me. Remove in the hygiene pass / by the admin.
- **`Vincent's Code/` removal** was blocked during C5 (the dir is owned by `matthieu`; the C5 process
  ran as `vincent`, so `rm` was permission-denied — the clone is intact). It is gitignored and not
  canonical, so it does not affect the gate; its README should be flagged as reference-only (see the
  banner queued for `matthieu` to apply). Remove it as `matthieu` (then drop its `pyproject`/
  `.gitignore` excludes) whenever convenient. Not blocking.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-06 | short intent |`
