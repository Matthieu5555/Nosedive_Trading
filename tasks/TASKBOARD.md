# TASKBOARD

The in-repo collision guard for a shared workspace with several humans and agents
working at once. **Before you start changing files, claim them here.** Clear your
claim when you're done. This is advisory, not enforced — it only works if every
actor reads and writes it, which is why `AGENTS.md` tells you to. The real safety
is branch discipline (one branch per task, merge small and often); the board is
the early warning that two of you are about to collide.

When a task is finished, move its line to `tasks/archive/` (create it when first
needed) with a one-line note on what was done, so "why was this changed" stays
answerable later.

## In flight

| Who | Area / files | Branch | Claimed | Note |
|-----|--------------|--------|---------|------|
| agent-A (claude) | backend/src/{contracts,config,provenance,storage,fixtures}, backend/tests, configs/, backend/pyproject.toml; +doc refresh AGENTS.md/.agent/map.md | feat/foundation | 2026-05-31 | Workstream A keystone. Gate green (ruff/mypy/pytest, 79 tests). Fixed storage Hive-partition re-read + QC partitioning, added pytz, documented schema-evolution rules (storage/README). Consolidating; not yet committed. |

## Format

`| your-name-or-agent | backend/foo.py, backend/bar.py | feat/foo | 2026-05-31 | short intent |`

## Planned workstreams

The volatility/risk backbone is cut into five orthogonal workstreams, one agent
each. Specs are self-contained in the files below. They talk only through the
typed contracts owned by Workstream A, so A lands first and the rest fan out.
Claim a workstream in the table above before you start; one branch per workstream.

Before writing tests in any workstream, read [TESTING.md](TESTING.md) — the
shared test-surface contract. It carries the cross-cutting rules (independent
oracles, the determinism mechanism, seam/contract tests, property tests, the
edge-case and coverage floors); each spec's own **Test surface** section names
the cases specific to its modules. Code without the named tests is not done.

| # | Workstream | Spec | Branch | Owns (dirs) | Depends on |
|---|------------|------|--------|-------------|------------|
| A | Foundation & data platform | [01-foundation-data-platform.md](01-foundation-data-platform.md) | feat/foundation | contracts/config pkg, `backend/src/storage`, `configs/`, tests scaffold | — (keystone) |
| B | Market-data plane | [02-market-data-plane.md](02-market-data-plane.md) | feat/market-data-plane | `src/connectivity`, `src/universe`, `src/collectors` | A |
| C | Analytics core | [03-analytics-core.md](03-analytics-core.md) | feat/analytics-core | `src/snapshots`, `src/forwards`, `src/iv`, `src/surfaces`, `src/pricing` | A |
| D | Risk engine | [04-risk-engine.md](04-risk-engine.md) | feat/risk-engine | `src/risk` | A, C (pricing iface) |
| E | Integration & operations | [05-integration-operations.md](05-integration-operations.md) | feat/integration-ops | `src/orchestration`, `src/qc`, actor module, `docs/` | A, B, C, D |
