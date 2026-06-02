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
| agent-A (claude) | backend/src/{contracts,config,provenance,storage,fixtures}, backend/tests, configs/, backend/pyproject.toml; +doc refresh AGENTS.md/.agent/map.md, ADR 0002 | feat/foundation | 2026-06-01 | Workstream A keystone. Gate green (ruff/mypy/pytest, 95 tests). Landed deep-modules review fixes: full-key lineage refs (no event-id conflation), all-or-nothing staged writes, schema-evolution enforced on read, single `validate_stamp`, narrowed contracts surface — see ADR 0002 and storage/README. Not yet committed. |
| agent-B (claude) | backend/src/{connectivity,universe,collectors}, backend/tests/test_{connectivity,universe,collectors,seam_market_data,smoke_bootstrap}.py; +per-dir READMEs, .agent/map.md row, ADR 0003 | feat/market-data-plane | 2026-06-01 | Workstream B market-data plane (steps 2–3 + IBKR-session part of step 1). On A's uncommitted hardening; will stage only B-owned files (no A/C edits, no pyproject change). Broker-agnostic session seam + one-place backoff/reconnect supervisor + client-id convention; universe (deterministic dedup, four accessors, InstrumentMaster materialization); append-only loss-aware collector (deterministic event_id idempotency, gap events, daily summary); step-1 smoke. No order placement. DONE: B's ruff/mypy/pytest green — 70 B tests incl. the non-negotiable kill-and-restart, deterministic universe dedup (cross-process), broker-agnostic seam, B→A round-trip; 165 green with A's suite. Docs: 3 per-dir READMEs + map.md row + ADR 0003. Not yet committed; will stage only B-owned files. NB shared tree also holds C's in-flight src/pricing (2 ruff E501s) — not B's, untouched. |
| agent-D (claude) | backend/src/risk/** (new), backend/tests/test_{risk,scenario,risk_properties,seam_risk,determinism_risk}.py (new), backend/tests/golden/risk_pf_risk.json (new); +src/risk/README.md, .agent/map.md row, ADR 0006; +1 line in backend/pyproject.toml ([tool.coverage] source += src/risk); +src/fixtures/positions.py (named pf-risk + low-confidence/multi-currency fixtures) | feat/risk-engine | 2026-06-02 | Workstream D risk engine (steps 11–12). Builds against C's frozen pricing interface (src/pricing present in shared tree) + A's contracts/fixtures. Foundation-first: one shared versioned bump source + monetization convention + pricing adapter + RiskAggregate/ScenarioResult assembly, frozen before Greeks/scenario build on it. DONE: gate green — ruff/mypy/pytest clean, 65 D tests (434 with full suite), src/risk branch coverage 96.4% (floor 90%; the 98.5% figure is total pure-core C+D, not risk-only). Oracle burst (3 agents; GBSM≡QuantLib≡py_vollib to ~1e-14) seeded test constants. Adversarial verification (3 agents) confirmed Greeks/monetization, aggregation, determinism; fixes landed for the real defects found — scenario grid de-dupes shocks + guards id collisions (was 2x-counting worst-case on duplicate shocks); effective_scenario_version folds a hash of D's grid-construction constants (ROLL_DOWN_DAYS/crash rule) so two grids can't share a version; reconcile surfaces a non-finite broker Greek; + carry≠0 and non-100-multiplier regression tests. ADR 0006 + README + map row. Working in shared tree like B; will stage only D-owned files onto feat/risk-engine (no A/C edits beyond the one pyproject coverage line + the new fixtures file). Not yet committed. |

| agent-E (claude) | backend/src/{actor,qc,orchestration}/** (new), backend/tests/test_{actor,qc,orchestration,replay,reconstruction,provenance_verification,replay_byte_identical,handover_e2e}*.py (new), docs/** (new); +.agent/map.md row, ADR(s) 0007+, backend/pyproject.toml ([project] deps: structlog/prometheus-client/apscheduler) + uv.lock | feat/integration-ops | 2026-06-02 | Workstream E integration & operations (steps 13–16 + Part IV.F run sequence + Part VI runbooks). Wave structure around the actor keystone: actor seam frozen first, then S1 actor / S2 qc / S3 observability concurrent, then S4 replay+reconstruction / S5 orchestration jobs. Two headline tests (same-code-path byte-identical replay; provenance verification over all C/D outputs in storage) owned foreground. Stages only E-owned files; no A/B/C/D source edits; does NOT touch [tool.coverage] source (E is behavior-tested per ADR 0004 §3). Added 3 op deps via uv. IN PROGRESS. |

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
