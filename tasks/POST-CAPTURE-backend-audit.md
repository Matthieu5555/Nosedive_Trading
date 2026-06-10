# POST-CAPTURE-backend-audit — full backend audit once the capture is banking days

> **QUEUED — run once the daily capture is operational and ≥2 clean days are banked** (so the
> audit checks the live system against real captured output, not just fixtures). A broad,
> multi-agent read-and-verify pass over the whole backend. Owner-requested 2026-06-10 after the
> capture-fixing run.

- **Scope:** all of `packages/` (infra, core, infra-{ibkr,saxo,deribit}) + `apps/frontend` BFF
  (the read/serialize layer) + `scripts/`. Read-and-verify only in the first pass — produces a
  findings report with `file:line` + severity + suggested fix; **no code changes** until the
  owner rules the findings (like the ingestion audit → ADR 0039/0040/0041 flow).
- **Builds on:** the prior audits — [AUDIT-code-postphase2-2026-06-07](AUDIT-code-postphase2-2026-06-07.md),
  [AUDIT-library-leverage-2026-06-07](AUDIT-library-leverage-2026-06-07.md),
  [AUDIT-tasks-coherence-2026-06-07](AUDIT-tasks-coherence-2026-06-07.md) — and **this session's
  ingestion-fixing run** (the audit must confirm those fixes landed coherently, not as a new layer
  of patches): [[../.agent/decisions/0039-raw-schema-bridge-and-sample-regeneration.md]],
  0040 (ingestion invariants), 0041 (overwrite re-fire), the 429 transport backoff, the front
  `ALGOTRADING_DATA_ROOT` override.
- **Depends on:** the capture running (banked SX5E + SPX days) so "conformity expected-vs-result"
  can compare live captured output against the blueprint/ADR expectations, not only synthetic
  fixtures.

## Why now

A fast, pressured fixing run (2026-06-10) landed several backend changes to get the close-capture
operational (raw-landing 429 fix, overwrite re-fire, slot purge, schema bridge). The owner wants a
**deliberate, comprehensive audit** afterwards to confirm the backend is coherent, idiomatic, and
conformant — and to catch anything the rushed run left rough — before building further on it.

## Audit dimensions (one work-stream each — fan out)

1. **Code-logic correctness.** Per subsystem (capture, collectors, actor/analytics, surfaces,
   risk, qc, orchestration, storage, BFF): does the code do what its docstring/spec/ADR says?
   Hunt look-ahead leaks (run `check-lookahead-bias`), silent-skip/empty paths (cf. ADR 0040 #3),
   off-by-one / unit / sign errors, partial-state persistence. Adversarially verify each finding.
2. **Conformity — expected vs result.** Compare **live captured output** (the banked SX5E/SPX
   days) against the blueprint data-dictionary (Part IX), the ADR contracts, and `tasks/*` specs:
   field names (ADR 0029), `$`-Greek units (0036), provenance/`config_hashes` (0028), surface/IV
   conventions. Flag every drift between what a spec promises and what the store actually holds.
3. **Library leverage — fluid & coherent use.** Continue [AUDIT-library-leverage]: where is plumbing
   hand-rolled that a declared lib does better (duckdb/polars predicate pushdown, pydantic at the
   BFF/config seams, scipy, httpx retry/transport)? Are libs used consistently (one as-of seam, one
   HTTP transport, one config model) or several ad-hoc ways? Feed the REP backlog.
4. **Best practices & idioms.** Error handling (loud-not-silent, ADR 0019/Part XIX), logging
   (structured, one correlation_id), typing (mypy strictness, no `Any` leaks), dataclass/contract
   discipline, test quality (independently-derived oracles, not golden-only), docstring/README
   freshness (the doc-freshness gate). Run `review-module-depth` on the deep seams.
5. **Operational robustness.** The capture path under the live gateway: rate-limit/backoff
   coverage beyond `secdef/info` (the 429 fix), session keepalive/expiry, disconnect alerting
   (the deferred Telegram/email route), restart/idempotency under the new overwrite model (ADR
   0041), retention/compaction triggers (ADR 0034, see [daily-bar-compaction](daily-bar-compaction.md)).

## Method

Multi-agent fan-out (one agent per dimension/subsystem), each returning structured findings
(`file:line`, severity, expected-vs-actual, suggested fix), **adversarially verified** before
inclusion. Synthesize into one report `tasks/AUDIT-backend-postcapture-<date>.md` reconciled
against the blueprint + ADRs. Then the owner rules; fixes become their own ADRs/tasks (no rushed
patching).

## Done when

A reconciled findings report exists, each finding rated (blueprint/ADR violation · drift · gap ·
nit) with `file:line` and a fix sketch; the rushed-run changes (0039/0040/0041 + 429 + env-root)
are each confirmed coherent or flagged; the highest-severity items are turned into ADRs/tasks and
prioritised. No code changed by the audit itself.
